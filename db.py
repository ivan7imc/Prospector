"""Persistência: Neon/Postgres se DATABASE_URL definida, senão SQLite local.

Neon-friendly: conexão curta por operação (serverless, sem pool ocioso
segurando o compute do Neon acordado) e autocommit.
"""
import os
from contextlib import closing

URL = os.getenv("DATABASE_URL", "")

if URL:
    import psycopg

    def _conn():
        # Neon exige TLS; se a URL não tiver sslmode, forçamos require
        dsn = URL if "sslmode=" in URL else \
            URL + ("&" if "?" in URL else "?") + "sslmode=require"
        return psycopg.connect(dsn, autocommit=True, connect_timeout=10)
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
