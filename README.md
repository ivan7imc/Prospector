# 🔎 Prospector

Web app ultra leve que mapeia a **presença digital de negócios locais**: busca um nicho no Google Maps, visita a ficha de cada estabelecimento, caça redes sociais nos sites e entrega um relatório categorizado (✅ com redes / 🌐 só site / ❌ sem nada) com export em Markdown e CSV.

**Stack:** Starlette + Playwright + httpx + Neon (Postgres) · ~700 linhas · 1 tela · sem framework JS

Otimizado para rodar no **Render free tier (512 MB)**: pico de RAM medido em **366–416 MB**.

---

## Rodando localmente

```bash
pip install -r requirements.txt
playwright install chromium --with-deps

# sem DATABASE_URL usa SQLite local (dev)
uvicorn app:app --port 8000
```

Abra http://localhost:8000, digite nicho + cidade e acompanhe o progresso ao vivo (SSE).

### Modo baixa memória (igual ao Render)

```bash
MALLOC_ARENA_MAX=2 LOW_MEM=1 PARALELO=1 LIMITE_MAX=40 \
  uvicorn app:app --port 8000 --workers 1 --no-access-log
```

---

## Deploy no Render + Neon

1. Crie um banco grátis em [neon.tech](https://neon.tech) e copie a connection string (use o endpoint **-pooler**)
2. No [Render](https://render.com): **New → Blueprint** → aponte para este repositório (lê o `render.yaml`)
3. Configure a env var `DATABASE_URL` com a connection string do Neon
4. Deploy — pronto

O Neon guarda jobs, cache (7 dias) e histórico, sobrevivendo a deploys e spin-downs do free tier.

---

## Variáveis de ambiente

| Variável | Default | Função |
|---|---|---|
| `DATABASE_URL` | — | Postgres/Neon; ausente → SQLite local |
| `LOW_MEM` | `0` | `1` = modo 512 MB (bloqueia imagens/CSS/tiles, sequencial, browser por etapa) |
| `PARALELO` | `5` (`1` se LOW_MEM) | Abas simultâneas |
| `LIMITE_MAX` | `120` (`40` se LOW_MEM) | Teto de lugares por job |
| `SINGLE_PROC` | `1` | `0` desliga `--single-process` do Chromium se instável |
| `MALLOC_ARENA_MAX` | — | Use `2` em containers com pouca RAM |

## API

| Rota | Método | Função |
|---|---|---|
| `/api/jobs` | POST `{nicho, cidade, limite?}` | Cria job (retorna cache se busca <7 dias) |
| `/api/jobs/{id}/events` | GET | SSE de progresso |
| `/api/jobs/{id}` | GET | Resultado JSON |
| `/api/jobs/{id}/export?fmt=md\|csv` | GET | Download do relatório |
| `/api/historico` | GET | Últimos 20 jobs |

## Estrutura

```
app.py             # Starlette: rotas + SSE + orquestração (retry por etapa)
scraper.py         # Google Maps: busca com rolagem infinita + fichas
social.py          # Caça de redes: httpx rápido → Playwright p/ sites JS
report.py          # Categorização + Markdown/CSV
db.py              # Neon/Postgres ou SQLite (fallback)
static/index.html  # UI completa (vanilla, dark mode, SSE)
```

## Avisos

- Raspagem do Google Maps pode violar os Termos de Serviço do Google. Use com volume baixo e por sua conta e risco; para produção séria considere a Places API oficial (basta substituir `scraper.py`).
- Os seletores do Maps estão centralizados no topo de `scraper.py` — se o Google mudar o DOM, o conserto é localizado.
