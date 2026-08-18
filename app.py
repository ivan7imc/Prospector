"""Prospector — Starlette puro (sem pydantic: -35 MB de RAM vs FastAPI)."""
import asyncio, json, os, time, uuid
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

import db
from scraper import Scraper
from social import cacar_sociais
from report import categorizar, normalizar_lugares, nome_arquivo, para_markdown, para_csv

db.init()
_eventos: dict[str, asyncio.Queue] = {}   # job_id -> fila SSE
_rodando = asyncio.Lock()                 # 1 job por vez (anti-bloqueio)
LIMITE_MAX = int(os.getenv("LIMITE_MAX", "40" if os.getenv("LOW_MEM") == "1" else "120"))


# caminho absoluto: não depende do diretório de onde o uvicorn foi iniciado
INDEX = Path(__file__).parent / "static" / "index.html"


async def index(request):
    return HTMLResponse(INDEX.read_text(encoding="utf-8"))


async def criar_job(request):
    try:
        body = await request.json()
        nicho = str(body["nicho"]).strip().lower()
        cidade = str(body["cidade"]).strip().lower()
        limite = max(5, min(int(body.get("limite", 40)), LIMITE_MAX))
        assert nicho and cidade
    except Exception:
        return JSONResponse({"erro": "envie nicho e cidade"}, status_code=422)

    # cache: mesma busca concluída nos últimos 7 dias
    rows = db.q("SELECT id FROM jobs WHERE nicho=? AND cidade=? AND status='ok' "
                "AND criado>? ORDER BY criado DESC LIMIT 1",
                (nicho, cidade, time.time() - 7 * 86400), fetch=True)
    if rows:
        return JSONResponse({"id": rows[0][0], "cache": True})
    jid = uuid.uuid4().hex[:12]
    db.q("INSERT INTO jobs VALUES(?,?,?,?,?,NULL)",
         (jid, nicho, cidade, "rodando", time.time()))
    _eventos[jid] = asyncio.Queue()
    asyncio.create_task(_executar(jid, nicho, cidade, limite))
    return JSONResponse({"id": jid, "cache": False})


async def _executar(jid, nicho, cidade, limite):
    fila = _eventos[jid]

    async def progresso(etapa, atual, total, msg):
        await fila.put({"etapa": etapa, "atual": atual, "total": total, "msg": msg})

    try:
        async with _rodando:
            await progresso("inicio", 0, 1, f"buscando {nicho} em {cidade}...")
            if os.getenv("LOW_MEM") == "1":
                # 512 MB: navegador novo por etapa — libera o renderer gordo.
                # retry 1x por etapa: --single-process pode crashar raramente
                async def etapa(fn):
                    for tentativa in (1, 2):
                        try:
                            async with Scraper() as s:
                                return await fn(s)
                        except Exception:
                            if tentativa == 2:
                                raise
                            await progresso("retry", 0, 1, "navegador reiniciado")

                lugares = await etapa(
                    lambda s: s.buscar_maps(nicho, cidade, limite, progresso))
                if not lugares:
                    raise ValueError("nenhum resultado no Maps")
                await etapa(lambda s: s.enriquecer(lugares, progresso))
                await etapa(lambda s: cacar_sociais(lugares, s.ctx, progresso))
            else:
                async with Scraper() as s:
                    lugares = await s.buscar_maps(nicho, cidade, limite, progresso)
                    if not lugares:
                        raise ValueError("nenhum resultado no Maps")
                    await s.enriquecer(lugares, progresso)
                    await cacar_sociais(lugares, s.ctx, progresso)
        cat = categorizar(lugares)
        resultado = {"nicho": nicho, "cidade": cidade, "lugares": lugares,
                     "resumo": {"com": len(cat["com"]), "so_site": len(cat["so_site"]),
                                "sem": len(cat["sem"]), "stats": cat["stats"]}}
        db.q("UPDATE jobs SET status='ok', resultado=? WHERE id=?",
             (json.dumps(resultado, ensure_ascii=False), jid))
        await fila.put({"etapa": "fim", "atual": 1, "total": 1, "msg": "concluído"})
    except Exception as e:
        db.q("UPDATE jobs SET status='erro', resultado=? WHERE id=?", (str(e), jid))
        await fila.put({"etapa": "erro", "atual": 0, "total": 1, "msg": str(e)[:200]})
    finally:
        # Remove a fila do dicionário assim que o job termina. Sem isso, se o
        # cliente nunca abrir /events (ou desconectar no meio), a entrada e os
        # eventos acumulados vazam para sempre. O consumidor que já está
        # conectado mantém sua própria referência `fila`, então continua
        # recebendo os eventos finais normalmente.
        _eventos.pop(jid, None)


async def eventos(request):
    jid = request.path_params["jid"]

    async def gen():
        fila = _eventos.get(jid)
        if not fila:                       # job já terminou (ou veio do cache)
            yield ("data: " + json.dumps(
                {"etapa": "fim", "atual": 1, "total": 1, "msg": "pronto"}) + "\n\n")
            return
        while True:
            try:
                # keepalive a cada 15s — o proxy do Render derruba SSE ocioso
                ev = await asyncio.wait_for(fila.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev["etapa"] in ("fim", "erro"):
                _eventos.pop(jid, None)
                return
    return StreamingResponse(gen(), media_type="text/event-stream")


def _carregar(jid):
    rows = db.q("SELECT status, resultado FROM jobs WHERE id=?", (jid,), fetch=True)
    if not rows:
        return None, JSONResponse({"erro": "job não encontrado"}, status_code=404)
    status, res = rows[0]
    if status == "erro":
        return None, JSONResponse({"erro": res}, status_code=500)
    if status != "ok":
        return None, JSONResponse({"erro": "ainda rodando"}, status_code=409)
    return json.loads(res), None


async def resultado(request):
    dados, erro = _carregar(request.path_params["jid"])
    return erro or JSONResponse(dados)


def _anexo(conteudo: str, nome: str, media: str) -> Response:
    """Força download: attachment + nosniff. Corpo em UTF-8 (CSV já vem com BOM)."""
    return Response(
        content=conteudo.encode("utf-8"),
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


async def exportar(request):
    jid = request.path_params["jid"]
    dados, erro = _carregar(jid)
    if erro:
        return erro
    try:
        cat = categorizar(dados.get("lugares") or [])
        fmt = (request.query_params.get("fmt") or "md").lower()
        nicho, cidade = dados.get("nicho", ""), dados.get("cidade", "")
        if fmt == "csv":
            return _anexo(para_csv(cat), nome_arquivo(nicho, cidade, "csv"), "text/csv")
        return _anexo(para_markdown(nicho, cidade, cat),
                      nome_arquivo(nicho, cidade, "md"), "text/markdown")
    except Exception as e:
        return JSONResponse({"erro": f"falha ao exportar: {e}"}, status_code=500)


async def lugares(request):
    """Exposição dos dados extraídos: lista plana e normalizada dos lugares.

    Filtros via query string:
      - q=          substring no nome (case-insensitive)
      - categoria=  com_redes | so_site | sem_nada
      - rede=       instagram | facebook | tiktok | linktree | whatsapp
      - limite/offset  paginação (limite default = todos, teto 500)
    """
    jid = request.path_params["jid"]
    dados, erro = _carregar(jid)
    if erro:
        return erro

    lista = normalizar_lugares(dados.get("lugares") or [])

    q = (request.query_params.get("q") or "").strip().lower()
    categoria = (request.query_params.get("categoria") or "").strip().lower()
    rede = (request.query_params.get("rede") or "").strip().lower()
    if q:
        lista = [l for l in lista if q in l["nome"].lower()]
    if categoria:
        lista = [l for l in lista if l["presenca"] == categoria]
    if rede:
        lista = [l for l in lista if rede in l["redes"]]

    try:
        offset = max(0, int(request.query_params.get("offset", 0)))
        limite = int(request.query_params.get("limite", len(lista)))
        limite = max(1, min(limite, 500))
    except ValueError:
        offset, limite = 0, len(lista)

    total = len(lista)
    return JSONResponse({"total": total, "offset": offset,
                         "limite": limite, "lugares": lista[offset:offset + limite]})


async def historico(request):
    rows = db.q("SELECT id, nicho, cidade, status, criado FROM jobs "
                "ORDER BY criado DESC LIMIT 20", fetch=True)
    return JSONResponse([{"id": r[0], "nicho": r[1], "cidade": r[2],
                          "status": r[3], "criado": r[4]} for r in rows])


app = Starlette(routes=[
    Route("/", index),
    Route("/api/jobs", criar_job, methods=["POST"]),
    Route("/api/jobs/{jid}/events", eventos),
    Route("/api/jobs/{jid}/export", exportar),
    Route("/api/jobs/{jid}/lugares", lugares),
    Route("/api/jobs/{jid}", resultado),
    Route("/api/historico", historico),
])
