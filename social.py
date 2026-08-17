"""Etapa 3: caça de redes sociais — httpx rápido, Playwright só para sites JS."""
import asyncio, os, re
from urllib.parse import urlparse
import httpx

PARALELO_BROWSER = int(os.getenv("PARALELO", "1" if os.getenv("LOW_MEM") == "1" else "4"))

SOCIAL_RE = re.compile(
    r'https?://(?:www\.|m\.|pt-br\.)?(?:instagram\.com|facebook\.com|fb\.com|'
    r'tiktok\.com|linktr\.ee|wa\.me|api\.whatsapp\.com)/[^\s"\'<>\\)\]}]*', re.I)
LIXO = re.compile(
    r'instagram\.com/(accounts|explore|about|legal|p|reel|reels|direct|api)(/|$)'
    r'|facebook\.com/(sharer|policies|help|login|tr)|/profilecard|/intent|badge', re.I)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}


def _host(u):
    h = urlparse(u).netloc.lower()
    for p in ("www.", "m.", "pt-br."):
        if h.startswith(p):
            h = h[len(p):]
    return h


def tipo_link(u):
    h = _host(u)
    if "instagram.com" in h: return "instagram"
    if "facebook.com" in h or h == "fb.com": return "facebook"
    if "tiktok.com" in h: return "tiktok"
    if "linktr.ee" in h: return "linktree"
    if "wa.me" in h or "whatsapp" in h: return "whatsapp"
    return None


def _norm(u):
    u = u.rstrip(".,;")
    if tipo_link(u) in ("instagram", "facebook", "tiktok", "linktree"):
        u = u.split("?")[0].rstrip("/")
        if "/profilecard" in u:
            u = u.split("/profilecard")[0]
    return u


def _filtra(urls):
    """Remove lixo e deduplica por (tipo, path)."""
    out = {}
    for u in urls:
        if LIXO.search(u):
            continue
        t = tipo_link(u)
        if not t:
            continue
        path = urlparse(u).path.strip("/").split("?")[0].lower()
        if not path and t not in ("whatsapp",):
            continue
        out.setdefault((t, path), _norm(u))
    # 1 WhatsApp no máximo
    wa = [v for (t, _), v in out.items() if t == "whatsapp"][:1]
    rest = [v for (t, _), v in out.items() if t != "whatsapp"]
    return sorted(set(rest)) + wa


async def _via_http(client, url):
    try:
        r = await client.get(url, timeout=8, headers=UA, follow_redirects=True)
        return _filtra(m.group(0) for m in SOCIAL_RE.finditer(r.text))
    except Exception:
        return []


async def _via_browser(ctx, url):
    page = await ctx.new_page()
    try:
        await page.goto(url, timeout=25000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)
        html = await page.content()
        hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        return _filtra(m.group(0) for m in SOCIAL_RE.finditer(html + " " + " ".join(hrefs)))
    except Exception:
        return []
    finally:
        await page.close()


async def cacar_sociais(lugares, ctx, progresso):
    """Preenche lug['sociais'] para cada lugar com website."""
    com_site = [l for l in lugares if l.get("website")]
    for l in lugares:
        l["sociais"] = []
        w = l.get("website")
        if w and tipo_link(w):          # link da ficha já é rede social
            l["sociais"] = _filtra([w])

    pendentes = [l for l in com_site if not l["sociais"]]
    feitos = 0

    # Passada 1: httpx (rápido, paralelo)
    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(10)

        async def http_um(l):
            nonlocal feitos
            async with sem:
                l["sociais"] = await _via_http(client, l["website"])
                feitos += 1
                await progresso("sociais", feitos, len(pendentes), f"{l['nome']} (http)")

        await asyncio.gather(*[http_um(l) for l in pendentes])

    # Passada 2: navegador, só para os que continuam vazios (sites JS)
    restam = [l for l in pendentes if not l["sociais"]]
    total2 = len(pendentes) + len(restam)

    if PARALELO_BROWSER <= 1:                # modo 512 MB: sequencial, 1 página
        for l in restam:
            l["sociais"] = await _via_browser(ctx, l["website"])
            feitos += 1
            await progresso("sociais", feitos, total2, f"{l['nome']} (navegador)")
        return lugares

    sem2 = asyncio.Semaphore(PARALELO_BROWSER)

    async def browser_um(l):
        nonlocal feitos
        async with sem2:
            l["sociais"] = await _via_browser(ctx, l["website"])
            feitos += 1
            await progresso("sociais", feitos, total2, f"{l['nome']} (navegador)")

    await asyncio.gather(*[browser_um(l) for l in restam])
    return lugares
