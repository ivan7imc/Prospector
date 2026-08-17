"""Etapa 4: categorização + exportação MD/CSV."""
import csv, io
from social import tipo_link

EMO = {"instagram": "📸", "facebook": "👥", "tiktok": "🎵", "linktree": "🔗", "whatsapp": "💬"}


def _knota(lug):
    try:
        return -float(lug.get("nota", "").replace(",", "."))
    except ValueError:
        return 0.0


def categorizar(lugares):
    com = sorted([l for l in lugares if l["sociais"]], key=_knota)
    so_site = sorted([l for l in lugares if not l["sociais"] and l.get("website")], key=_knota)
    sem = sorted([l for l in lugares if not l["sociais"] and not l.get("website")], key=_knota)
    stats = {}
    for l in com:
        for u in l["sociais"]:
            t = tipo_link(u)
            if t:
                stats[t] = stats.get(t, 0) + 1
    return {"com": com, "so_site": so_site, "sem": sem, "stats": stats}


def para_markdown(nicho, cidade, cat):
    com, so_site, sem = cat["com"], cat["so_site"], cat["sem"]
    total = len(com) + len(so_site) + len(sem)
    L = [f"# 🔎 {nicho.title()} em {cidade} — presença digital",
         f"\n**{total} estabelecimentos** (fonte: Google Maps)\n",
         "| Categoria | Qtde |", "|---|---|",
         f"| ✅ Com redes sociais | **{len(com)}** |",
         f"| 🌐 Só site/cardápio | {len(so_site)} |",
         f"| ❌ Sem nenhum link | {len(sem)} |",
         f"\n## ✅ Com redes sociais ({len(com)})\n"]
    for l in com:
        end = l["categoria_endereco"].split("·")[-1].strip()
        L.append(f"### {l['nome']}" + (f" — ⭐ {l['nota']}" if l["nota"] else ""))
        if end: L.append(f"- 📍 {end}")
        if l.get("tel"): L.append(f"- 📞 {l['tel']}")
        for u in l["sociais"]:
            t = tipo_link(u)
            L.append(f"- {EMO.get(t, '🔗')} {u}")
        L.append("")
    L.append(f"\n## 🌐 Só site/cardápio ({len(so_site)})\n\n| Nome | ⭐ | Link |\n|---|---|---|")
    for l in so_site:
        L.append(f"| {l['nome'][:45]} | {l['nota'] or '—'} | {(l.get('website') or '')[:70]} |")
    L.append(f"\n## ❌ Sem nenhum link ({len(sem)})\n\n| Nome | ⭐ | Telefone |\n|---|---|---|")
    for l in sem:
        L.append(f"| {l['nome'][:50]} | {l['nota'] or '—'} | {l.get('tel') or '—'} |")
    return "\n".join(L)


def para_csv(cat):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["categoria", "nome", "nota", "endereco", "telefone", "website", "redes_sociais"])
    for grupo, nome_g in ((cat["com"], "com_redes"), (cat["so_site"], "so_site"), (cat["sem"], "sem_nada")):
        for l in grupo:
            end = l["categoria_endereco"].split("·")[-1].strip()
            w.writerow([nome_g, l["nome"], l.get("nota", ""), end,
                        l.get("tel") or "", l.get("website") or "", " | ".join(l["sociais"])])
    return buf.getvalue()
