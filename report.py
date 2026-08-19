"""Etapa 4: categorização + exportação MD/CSV."""
import csv, io, re, unicodedata
from urllib.parse import urlparse
from social import tipo_link

EMO = {"instagram": "📸", "facebook": "👥", "tiktok": "🎵", "linktree": "🔗", "whatsapp": "💬"}


def _knota(lug):
    try:
        return -float((lug.get("nota") or "").replace(",", "."))
    except ValueError:
        return 0.0


def _sociais(lug):
    return list(lug.get("sociais") or [])


def _end(lug):
    return (lug.get("categoria_endereco") or "").split("·")[-1].strip()


def _partes_categoria_endereco(lug):
    partes = [p.strip() for p in (lug.get("categoria_endereco") or "").split("·") if p.strip()]
    return {
        "categoria": partes[0] if len(partes) > 1 else "",
        "endereco": partes[-1] if partes else "",
    }


def _slug(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "relatorio"


def _instagram_urls(lug):
    return [u for u in _sociais(lug) if tipo_link(u) == "instagram"]


def _instagram_username(url):
    """Extrai o @username de uma URL do Instagram já filtrada em social.py."""
    if not url:
        return ""
    path = urlparse(url).path.strip("/")
    if not path:
        return ""
    username = path.split("/")[0].strip().lstrip("@")
    # Defesa extra para endpoints que não representam perfis.
    if username.lower() in {"accounts", "explore", "about", "legal", "p", "reel", "reels", "direct", "api"}:
        return ""
    return username


def _redes(lug):
    tipos = []
    for u in _sociais(lug):
        t = tipo_link(u)
        if t and t not in tipos:
            tipos.append(t)
    return tipos


def nome_arquivo(nicho, cidade, ext):
    return f"prospector-{_slug(nicho)}-{_slug(cidade)}.{ext}"


def categorizar(lugares):
    com, so_site, sem = [], [], []
    for l in lugares or []:
        if _sociais(l):
            com.append(l)
        elif l.get("website"):
            so_site.append(l)
        else:
            sem.append(l)
    com.sort(key=_knota)
    so_site.sort(key=_knota)
    sem.sort(key=_knota)
    stats = {}
    for l in com:
        for u in _sociais(l):
            t = tipo_link(u)
            if t:
                stats[t] = stats.get(t, 0) + 1
    return {"com": com, "so_site": so_site, "sem": sem, "stats": stats}


def normalizar_lugares(lugares, nicho="", cidade=""):
    """Lista plana/normalizada dos lugares extraídos — p/ API e export Opportunity."""
    out = []
    for l in lugares or []:
        soc = _sociais(l)
        insta_urls = _instagram_urls(l)
        instagram = insta_urls[0] if insta_urls else ""
        if soc:
            presenca = "com_redes"
        elif l.get("website"):
            presenca = "so_site"
        else:
            presenca = "sem_nada"
        partes = _partes_categoria_endereco(l)
        redes = _redes(l)
        whatsapp_urls = [u for u in soc if tipo_link(u) == "whatsapp"]
        out.append({
            "nome": l.get("nome") or "",
            "nicho": nicho or "",
            "cidade": cidade or "",
            "nota": l.get("nota") or "",
            "nota_google": l.get("nota") or "",
            "avaliacoes_google": l.get("avaliacoes") or l.get("avaliacoes_google") or "",
            "categoria": partes["categoria"],
            "endereco": partes["endereco"],
            "horario": l.get("horario") or "",
            "telefone": l.get("tel") or "",
            "website": l.get("website") or "",
            "gmaps_url": l.get("gmaps_url") or "",
            "presenca": presenca,
            "instagram": instagram,
            "instagram_username": _instagram_username(instagram),
            "instagram_urls": insta_urls,
            "whatsapp": whatsapp_urls[0] if whatsapp_urls else "",
            "outras_redes": [u for u in soc if tipo_link(u) != "instagram"],
            "redes": redes,
            "sociais": soc,
        })
    return out


def para_markdown(nicho, cidade, cat):
    com, so_site, sem = cat["com"], cat["so_site"], cat["sem"]
    total = len(com) + len(so_site) + len(sem)
    L = [f"# 🔎 {str(nicho or '').title()} em {cidade} — presença digital",
         f"\n**{total} estabelecimentos** (fonte: Google Maps)\n",
         "| Categoria | Qtde |", "|---|---|",
         f"| ✅ Com redes sociais | **{len(com)}** |",
         f"| 🌐 Só site/cardápio | {len(so_site)} |",
         f"| ❌ Sem nenhum link | {len(sem)} |",
         f"\n## ✅ Com redes sociais ({len(com)})\n"]
    for l in com:
        end = _end(l)
        L.append(f"### {l.get('nome') or '—'}" + (f" — ⭐ {l['nota']}" if l.get("nota") else ""))
        if end:
            L.append(f"- 📍 {end}")
        if l.get("tel"):
            L.append(f"- 📞 {l['tel']}")
        for u in _sociais(l):
            t = tipo_link(u)
            L.append(f"- {EMO.get(t, '🔗')} {u}")
        L.append("")
    L.append(f"\n## 🌐 Só site/cardápio ({len(so_site)})\n\n| Nome | ⭐ | Link |\n|---|---|---|")
    for l in so_site:
        L.append(f"| {(l.get('nome') or '')[:45]} | {l.get('nota') or '—'} | {(l.get('website') or '')[:70]} |")
    L.append(f"\n## ❌ Sem nenhum link ({len(sem)})\n\n| Nome | ⭐ | Telefone |\n|---|---|---|")
    for l in sem:
        L.append(f"| {(l.get('nome') or '')[:50]} | {l.get('nota') or '—'} | {l.get('tel') or '—'} |")
    return "\n".join(L)


def para_csv(cat, nicho="", cidade=""):
    """CSV compatível com o Opportunity.

    Mantém campos humanos do Prospector e adiciona colunas normalizadas para que
    o Opportunity não precise inferir cidade, categoria, Instagram, username ou
    links de prospecção a partir de texto livre.
    """
    # BOM: Excel no Windows reconhece UTF-8 (Açúcar, Poços…)
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([
        "nome",
        "nicho",
        "cidade",
        "categoria",
        "endereco",
        "telefone",
        "website",
        "instagram",
        "instagram_username",
        "instagram_urls",
        "whatsapp",
        "nota_google",
        "avaliacoes_google",
        "gmaps_url",
        "horario",
        "presenca",
        "redes",
        "redes_sociais",
        "outras_redes",
        "fonte",
    ])
    for l in normalizar_lugares(cat["com"] + cat["so_site"] + cat["sem"], nicho, cidade):
        w.writerow([
            l["nome"],
            l["nicho"],
            l["cidade"],
            l["categoria"],
            l["endereco"],
            l["telefone"],
            l["website"],
            l["instagram"],
            l["instagram_username"],
            " | ".join(l["instagram_urls"]),
            l["whatsapp"],
            l["nota_google"],
            l["avaliacoes_google"],
            l["gmaps_url"],
            l["horario"],
            l["presenca"],
            " | ".join(l["redes"]),
            " | ".join(l["sociais"]),
            " | ".join(l["outras_redes"]),
            "google_maps_via_prospector",
        ])
    return buf.getvalue()
