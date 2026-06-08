from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

from app.config import get_settings


@contextmanager
def mysql_conn(database: str):
    settings = get_settings()
    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
    )
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(database: str, sql: str, params: tuple | dict | None = None) -> list[dict]:
    with mysql_conn(database) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall())


def fetch_one(database: str, sql: str, params: tuple | dict | None = None) -> dict | None:
    rows = fetch_all(database, sql, params)
    return rows[0] if rows else None
