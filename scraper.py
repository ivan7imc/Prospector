"""Etapas 1-2: busca no Google Maps + enriquecimento das fichas."""
import asyncio, json, os, re
import httpx
from playwright.async_api import async_playwright

# Modo baixa memória (Render free tier: 512 MB) — LOW_MEM=1
LOW_MEM = os.getenv("LOW_MEM", "0") == "1"
PARALELO = int(os.getenv("PARALELO", "1" if LOW_MEM else "5"))
CHROME_ARGS = [
    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
    "--no-zygote", "--disable-extensions", "--mute-audio",
    "--disable-background-networking", "--disable-sync",
    "--js-flags=--max-old-space-size=56",
]
if LOW_MEM:
    CHROME_ARGS += [
        "--renderer-process-limit=2",       # reusa renderers entre abas
        "--disable-site-isolation-trials",  # 1 processo p/ vários sites
        "--disable-webgl", "--disable-webgl2",  # mapa 3D é o maior gastador
        "--blink-settings=imagesEnabled=false",
        "--disable-features=IsolateOrigins,site-per-process,NetworkServiceInProcess",
        "--disk-cache-size=1",
        "--disable-remote-fonts", "--disable-speech-api",
        "--disable-notifications", "--force-color-profile=srgb",
    ]
    if os.getenv("SINGLE_PROC", "1") == "1":
        CHROME_ARGS.append("--single-process")   # -100 MB; desligável se instável
    else:
        # multi-processo: low-end-mode ajuda (conflita com --single-process)
        CHROME_ARGS.append("--enable-low-end-device-mode")

# Tiles do mapa (vector/raster) — inúteis p/ nós, e são o grosso da RAM do renderer
_TILE_PAT = ("/maps/vt", "/vt/pb", "PlanetTile", "/kh/", "streetview")


async def _bloqueia_pesados(route):
    """Aborta imagens/mídia/fontes/CSS e tiles do mapa — corta a RAM por página.
    CSS é seguro de bloquear: extraímos via seletores DOM, não visual."""
    req = route.request
    if req.resource_type in {"image", "media", "font", "stylesheet"} or \
       any(p in req.url for p in _TILE_PAT):
        await route.abort()
    else:
        await route.continue_()

# Seletores centralizados (quando o Google mudar o DOM, conserta-se aqui)
SEL_FEED = 'div[role="feed"]'
SEL_CARD_LINK = 'div[role="feed"] a[href*="/maps/place/"]'
SEL_WEBSITE = 'a[data-item-id="authority"]'
SEL_PHONE = 'button[data-item-id^="phone:tel:"]'
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


async def geocode(cidade: str) -> tuple[float, float]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://nominatim.openstreetmap.org/search",
                        params={"q": cidade, "format": "json", "limit": 1},
                        headers={"User-Agent": "prospector/1.0"})
        d = r.json()
        if not d:
            raise ValueError(f"Cidade não encontrada: {cidade}")
        return float(d[0]["lat"]), float(d[0]["lon"])


class Scraper:
    """Mantém 1 Chromium vivo para todo o job."""

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=True, args=CHROME_ARGS)
        self.ctx = await self.browser.new_context(
            locale="pt-BR", user_agent=UA,
            viewport={"width": 720, "height": 520} if LOW_MEM else None)
        if LOW_MEM:
            await self.ctx.route("**/*", _bloqueia_pesados)
        return self

    async def __aexit__(self, *_):
        await self.browser.close()
        await self._pw.stop()

    async def buscar_maps(self, nicho: str, cidade: str, limite: int, progresso):
        """Etapa 1: busca + rolagem infinita. Retorna lista de lugares."""
        lat, lon = await geocode(cidade)
        url = (f"https://www.google.com/maps/search/"
               f"{nicho.replace(' ', '+')}+{cidade.replace(' ', '+')}/@{lat},{lon},14z?hl=pt-BR")
        page = await self.ctx.new_page()
        try:
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(6000)
            prev = 0
            for i in range(25):
                estado = await page.evaluate(f"""() => {{
                    const feed = document.querySelector('{SEL_FEED}');
                    if (!feed) return {{n: 0, fim: true}};
                    feed.scrollTo(0, feed.scrollHeight);
                    return {{
                        n: document.querySelectorAll('{SEL_CARD_LINK}').length,
                        fim: /final da lista|end of the list/i.test(feed.innerText)
                    }};
                }}""")
                await progresso("busca", estado["n"], limite,
                                f"rolagem {i+1}: {estado['n']} lugares")
                if estado["fim"] or estado["n"] >= limite or (i > 3 and estado["n"] == prev):
                    break
                prev = estado["n"]
                await page.wait_for_timeout(2000)

            brutos = await page.evaluate(f"""() => {{
                const out = [];
                document.querySelectorAll('{SEL_CARD_LINK}').forEach(a => {{
                    const nome = a.getAttribute('aria-label');
                    if (!nome) return;
                    let texto = '';
                    let el = a.parentElement;
                    for (let k = 0; k < 4 && el; k++) {{
                        if (el.innerText && el.innerText.length > 40) {{ texto = el.innerText; break; }}
                        el = el.parentElement;
                    }}
                    out.push({{nome, texto, url: a.href}});
                }});
                return out;
            }}""")
        finally:
            await page.close()

        vistos, lugares = set(), []
        for b in brutos[:limite * 2]:
            if b["nome"] in vistos:
                continue
            vistos.add(b["nome"])
            lugares.append({**_parse_card(b["nome"], b["texto"]), "gmaps_url": b["url"]})
            if len(lugares) >= limite:
                break
        return lugares

    async def enriquecer(self, lugares: list, progresso, paralelo=None):
        """Etapa 2: visita a ficha de cada lugar (site + telefone).
        Em LOW_MEM (paralelo=1) roda sequencial reusando UMA página."""
        n_par = paralelo or PARALELO
        feitos = 0

        async def extrai(page, lug):
            try:
                await page.goto(lug["gmaps_url"], timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2200)
                el = await page.query_selector(SEL_WEBSITE)
                lug["website"] = await el.get_attribute("href") if el else None
                tel = await page.query_selector(SEL_PHONE)
                lug["tel"] = ((await tel.get_attribute("data-item-id"))
                              .replace("phone:tel:", "") if tel else None)
                if LOW_MEM:   # descarrega o doc pesado do Maps entre fichas
                    await page.goto("about:blank")
            except Exception:
                lug["website"], lug["tel"] = None, None

        if n_par <= 1:                       # modo 512 MB: 1 página reusada
            page = await self.ctx.new_page()
            try:
                for i, lug in enumerate(lugares):
                    if "tel" in lug:         # já processada (retry pós-crash)
                        feitos += 1
                        continue
                    if i and i % 5 == 0:     # recicla a página: zera heap JS
                        await page.close()
                        page = await self.ctx.new_page()
                    await extrai(page, lug)
                    feitos += 1
                    await progresso("fichas", feitos, len(lugares), lug["nome"])
            finally:
                await page.close()
            return lugares

        sem = asyncio.Semaphore(n_par)

        async def um(lug):
            nonlocal feitos
            async with sem:
                page = await self.ctx.new_page()
                try:
                    await page.goto(lug["gmaps_url"], timeout=30000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2200)
                    el = await page.query_selector(SEL_WEBSITE)
                    lug["website"] = await el.get_attribute("href") if el else None
                    tel = await page.query_selector(SEL_PHONE)
                    if tel:
                        did = await tel.get_attribute("data-item-id")
                        lug["tel"] = did.replace("phone:tel:", "")
                    else:
                        lug["tel"] = None
                except Exception:
                    lug["website"], lug["tel"] = None, None
                finally:
                    await page.close()
                feitos += 1
                await progresso("fichas", feitos, len(lugares), lug["nome"])

        await asyncio.gather(*[um(l) for l in lugares])
        return lugares


def _parse_card(nome: str, texto: str) -> dict:
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]
    nota, avaliacoes, cat_end, horario = "", "", "", ""
    for l in linhas:
        if l == nome:
            continue
        if not nota and re.match(r"^\d,\d", l):
            nota = l.split()[0][:3]
            m = re.search(r"\(([\d\.,]+)\)", l)
            if m:
                avaliacoes = m.group(1).replace(".", "")
        elif not cat_end and "·" in l:
            cat_end = l
        elif not horario and re.match(r"^(Aberto|Fechado|Fecha)", l):
            horario = l
    return {
        "nome": nome,
        "nota": nota,
        "avaliacoes": avaliacoes,
        "categoria_endereco": cat_end,
        "horario": horario,
    }
