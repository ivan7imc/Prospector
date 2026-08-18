"""Etapa 4: categorização + exportação MD/CSV."""
import csv, io, re, unicodedata
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


def _slug(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "relatorio"


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


def normalizar_lugares(lugares):
    """Lista plana/normalizada dos lugares extraídos — p/ exposição via API."""
    out = []
    for l in lugares or []:
        soc = _sociais(l)
        if soc:
            presenca = "com_redes"
        elif l.get("website"):
            presenca = "so_site"
        else:
            presenca = "sem_nada"
        partes = (l.get("categoria_endereco") or "").split("·")
        out.append({
            "nome": l.get("nome") or "",
            "nota": l.get("nota") or "",
            "categoria": partes[0].strip() if len(partes) > 1 else "",
            "endereco": partes[-1].strip(),
            "horario": l.get("horario") or "",
            "telefone": l.get("tel") or "",
            "website": l.get("website") or "",
            "gmaps_url": l.get("gmaps_url") or "",
            "presenca": presenca,
            "redes": [tipo_link(u) for u in soc],
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


def para_csv(cat):
    # BOM: Excel no Windows reconhece UTF-8 (Açúcar, Poços…)
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["categoria", "nome", "nota", "endereco", "telefone", "website", "redes_sociais"])
    for grupo, nome_g in ((cat["com"], "com_redes"), (cat["so_site"], "so_site"), (cat["sem"], "sem_nada")):
        for l in grupo:
            w.writerow([nome_g, l.get("nome") or "", l.get("nota") or "", _end(l),
                        l.get("tel") or "", l.get("website") or "", " | ".join(_sociais(l))])
    return buf.getvalue()
