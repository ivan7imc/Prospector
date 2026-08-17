"""Persistência: Neon/Postgres se DATABASE_URL definida, senão SQLite local.

Neon-friendly: conexão curta por operação (serverless, sem pool ocioso
segurando o compute do Neon acordado) e autocommit.

Cold start: no free tier o compute do Neon suspende após ~5 min ocioso e o
primeiro acesso precisa acordá-lo. Isso costuma levar poucos segundos, mas
pode passar de 10s — daí o timeout generoso e um retry, já que durante o
wake o endpoint pode recusar/derrubar a conexão antes de aceitar.
"""
import os
import time
from contextlib import closing

URL = os.getenv("DATABASE_URL", "")

# margem para o wake do compute suspenso (Render/Neon free tier)
PG_TIMEOUT = int(os.getenv("PG_CONNECT_TIMEOUT", "30"))
PG_TENTATIVAS = int(os.getenv("PG_TENTATIVAS", "3"))

if URL:
    import psycopg

    def _dsn():
        dsn = URL
        # Neon exige TLS; se a URL não tiver sslmode, forçamos require
        if "sslmode=" not in dsn:
            dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
        # respeita connect_timeout explícito na URL; senão aplica o nosso
        if "connect_timeout=" not in dsn:
            dsn += ("&" if "?" in dsn else "?") + f"connect_timeout={PG_TIMEOUT}"
        return dsn

    def _conn():
        """Conecta com retry: o 1º acesso após o compute dormir pode falhar."""
        for tentativa in range(1, PG_TENTATIVAS + 1):
            try:
                return psycopg.connect(_dsn(), autocommit=True)
            except psycopg.OperationalError:
                if tentativa == PG_TENTATIVAS:
                    raise
                # backoff curto: o compute do Neon costuma subir em segundos
                time.sleep(tentativa)
else:
    import sqlite3

    def _conn():
        c = sqlite3.connect("prospector.db")
        c.isolation_level = None          # autocommit
        return c


def q(sql, params=(), fetch=False):
    """Executa SQL com placeholders '?' (traduzidos p/ '%s' no Postgres)."""
    if URL:
        sql = sql.replace("?", "%s")
    with closing(_conn()) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if fetch else None


def init():
    q("""CREATE TABLE IF NOT EXISTS jobs(
        id TEXT PRIMARY KEY, nicho TEXT, cidade TEXT, status TEXT,
        criado DOUBLE PRECISION, resultado TEXT)""")
