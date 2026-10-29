"""
Unidade de trabalho do turno: as tools que escrevem no Postgres usam uma
conexão própria do turno e não confirmam. Quem confirma é o fechamento do
turno (graph.py), e só se o juiz autorizou. Fora de um turno (MCP, scripts)
nada muda: as tools seguem com a conexão global e o commit imediato.
"""
from contextvars import ContextVar

import psycopg2

from app.config import URL_DB

# Dict mutável: as threads das tools copiam o contexto e enxergam o mesmo dict.
_turno: ContextVar = ContextVar("turno_db", default=None)


def abrir_turno_db() -> None:
    _turno.set({"conn": None})


def conexao_do_turno():
    """Conexão do turno (criada na primeira escrita/leitura), ou None fora de turno."""
    turno = _turno.get()
    if turno is None:
        return None
    if turno["conn"] is None:
        turno["conn"] = psycopg2.connect(
            URL_DB, options="-c idle_in_transaction_session_timeout=30000"
        )
    with turno["conn"].cursor() as cur:
        cur.execute("SAVEPOINT tool")
    return turno["conn"]


def _no_turno(conn) -> bool:
    turno = _turno.get()
    return turno is not None and conn is turno["conn"]


def confirmar(conn) -> None:
    """No lugar de conn.commit() nas tools: no turno, quem confirma é o fechamento."""
    if not _no_turno(conn):
        conn.commit()


def desfazer(conn) -> None:
    """No lugar de conn.rollback() nas tools: no turno, desfaz só a chamada que falhou."""
    if _no_turno(conn):
        with conn.cursor() as cur:
            cur.execute("ROLLBACK TO SAVEPOINT tool")
    else:
        conn.rollback()


def liberar(conn) -> None:
    """No lugar de conn.close() nas tools: a conexão do turno só fecha no fechamento."""
    if not _no_turno(conn):
        conn.close()


def encerrar_turno_db(autorizado: bool) -> None:
    turno = _turno.get()
    _turno.set(None)
    conn = turno["conn"] if turno else None
    if conn is None:
        return
    try:
        conn.commit() if autorizado else conn.rollback()
    finally:
        conn.close()
