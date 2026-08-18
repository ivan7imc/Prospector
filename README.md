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

> **Cold start:** no free tier o compute do Neon suspende após alguns minutos ocioso.
> O primeiro acesso precisa acordá-lo, então `db.py` usa `connect_timeout=30` e
> tenta reconectar 3x (backoff 1s/2s). Ajuste com `PG_CONNECT_TIMEOUT`/`PG_TENTATIVAS`.
>
> Cole a connection string com `&` puro — se vier escapada como `&amp;` (copiar/colar
> de HTML), o psycopg falha com `invalid URI query parameter: "amp;..."`.

---

## Variáveis de ambiente

| Variável | Default | Função |
|---|---|---|
| `DATABASE_URL` | — | Postgres/Neon; ausente → SQLite local |
| `PG_CONNECT_TIMEOUT` | `30` | Segundos p/ conectar; margem para o cold start do Neon (ignorado se a URL já tiver `connect_timeout`) |
| `PG_TENTATIVAS` | `3` | Tentativas de conexão (backoff 1s, 2s) enquanto o compute do Neon acorda |
| `LOW_MEM` | `0` | `1` = modo 512 MB (bloqueia imagens/CSS/tiles, sequencial, browser por etapa) |
| `PARALELO` | `5` (`1` se LOW_MEM) | Abas simultâneas |
| `LIMITE_MAX` | `120` (`40` se LOW_MEM) | Teto de lugares por job |
| `SINGLE_PROC` | `1` | `0` desliga `--single-process` do Chromium se instável |
| `MALLOC_ARENA_MAX` | — | Use `2` em containers com pouca RAM |

## API

A API expõe os dados extraídos em JSON e permite baixar o relatório em Markdown/CSV.
Todas as rotas ficam sob `/api`. Base local: `http://localhost:8000`.

### `POST /api/jobs`

Cria um job de raspagem. Se a mesma busca (nicho + cidade) foi concluída nos
últimos 7 dias, retorna o job em cache sem raspar de novo.

**Corpo** (JSON):

```json
{ "nicho": "cafeterias", "cidade": "curitiba", "limite": 40 }
```

| Campo | Tipo | Obrigatório | Função |
|---|---|---|---|
| `nicho` | string | ✅ | Nicho/termo de busca |
| `cidade` | string | ✅ | Cidade (geocodada via Nominatim) |
| `limite` | int | — | Teto de lugares (5–`LIMITE_MAX`); default 40 |

**Resposta** — `200 OK`:

```json
{ "id": "3f2a9c8b7d1e", "cache": false }
```

`cache: true` indica que veio de uma busca recente.

### `GET /api/jobs/{id}/events`

Stream de progresso via **Server-Sent Events (SSE)** — use `EventSource` no navegador
ou `text/event-stream` no cliente. Cada evento tem `etapa`, `atual`, `total` e `msg`:

```
data: {"etapa": "busca", "atual": 12, "total": 40, "msg": "rolagem 3: 12 lugares"}

data: {"etapa": "fim", "atual": 1, "total": 1, "msg": "concluído"}
```

A stream termina em `etapa: "fim"` (sucesso) ou `etapa: "erro"`.

### `GET /api/jobs/{id}`

Resultado completo do job em JSON — inclui a lista bruta de `lugares` (nome, nota,
categoria/endereço, horário, telefone, website, `gmaps_url` e `sociais`) e um `resumo`
com a contagem por categoria e o ranking de redes.

```json
{
  "nicho": "cafeterias",
  "cidade": "curitiba",
  "resumo": { "com": 12, "so_site": 8, "sem": 5, "stats": { "instagram": 11 } },
  "lugares": [ { "nome": "...", "nota": "4,6", "sociais": ["https://instagram.com/..."] } ]
}
```

### `GET /api/jobs/{id}/lugares`

Lista **plana e normalizada** dos lugares extraídos — o endpoint indicado para
consumir os dados. Cada lugar já vem com a categoria/endereço separados e o campo
`presenca` (classificação da presença digital).

| Campo | Descrição |
|---|---|
| `nome`, `nota`, `horario`, `telefone` | Dados da ficha no Maps |
| `categoria`, `endereco` | Separados a partir de `categoria_endereco` |
| `website`, `gmaps_url` | Links |
| `presenca` | `com_redes` \| `so_site` \| `sem_nada` |
| `redes` | Tipos de rede (ex.: `["instagram", "whatsapp"]`) |
| `sociais` | URLs completas das redes |

**Filtros** (query string, combináveis):

| Parâmetro | Função | Exemplo |
|---|---|---|
| `q` | Substring no nome (case-insensitive) | `?q=café` |
| `categoria` | Filtra por presença digital | `?categoria=com_redes` |
| `rede` | Filtra por rede social | `?rede=instagram` |
| `limite` / `offset` | Paginação (teto de 500) | `?limite=20&offset=20` |

**Resposta** — `200 OK`:

```json
{
  "total": 25, "offset": 0, "limite": 20,
  "lugares": [
    {
      "nome": "Café Alameda", "nota": "4,6", "categoria": "Cafeteria",
      "endereco": "Rua XV de Novembro, 300", "telefone": "+55 41 ...",
      "website": "https://...", "gmaps_url": "https://maps/...",
      "presenca": "com_redes", "redes": ["instagram"], "sociais": ["https://instagram.com/..."]
    }
  ]
}
```

### `GET /api/jobs/{id}/export?fmt=md|csv`

Download do relatório como anexo (`Content-Disposition: attachment`).
Arquivo `prospector-{nicho}-{cidade}.{md,csv}` — CSV em UTF-8 com BOM (abre direto no Excel).

### `GET /api/historico`

Últimos 20 jobs (qualquer status), mais recentes primeiro.

```json
[ { "id": "3f2a9c8b7d1e", "nicho": "cafeterias", "cidade": "curitiba", "status": "ok", "criado": 1723824000 } ]
```

### Erros

| Status | Quando |
|---|---|
| `404` | Job não encontrado |
| `409` | Job ainda rodando |
| `422` | POST sem `nicho`/`cidade` (ou `limite` inválido) |
| `500` | Job falhou (detalhe no corpo `{ "erro": "..." }`) |

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
