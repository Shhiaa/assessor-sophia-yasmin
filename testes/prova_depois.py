"""
Prova a invariante nas três tools de escrita, sem LLM e sem JEV:
turno recusado não deixa linha; turno aprovado grava uma única vez.

Rode da raiz do projeto, com o .env preenchido:

    python testes/prova_depois.py

Cada tentativa usa um marcador único (ZZ-<hora>-<n>). As linhas dos turnos
aprovados ficam no banco como evidência (marcador ZZ-...).
"""
import contextvars
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402

from app.config import URL_DB  # noqa: E402
from app.tools.agenda import add_event  # noqa: E402
from app.tools.financeiro import add_transaction, update_transaction  # noqa: E402
from app.unidade_trabalho import abrir_turno_db, encerrar_turno_db  # noqa: E402

RODADA = time.strftime("%H%M%S")
falhas = []


def contar(sql: str, marcador: str) -> int:
    """Consulta por uma conexão separada: só enxerga o que foi confirmado."""
    conn = psycopg2.connect(URL_DB)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (marcador,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def conferir(nome: str, obtido, esperado) -> None:
    ok = obtido == esperado
    print(f"{'OK  ' if ok else 'FALHA'} {nome}: obtido={obtido} esperado={esperado}")
    if not ok:
        falhas.append(nome)


def turno(escrever, autorizado: bool):
    abrir_turno_db()
    resultado = escrever()
    encerrar_turno_db(autorizado)
    return resultado


TX = "SELECT count(*) FROM transactions WHERE source_text = %s"
TX_DESC = "SELECT count(*) FROM transactions WHERE description = %s"
EV = "SELECT count(*) FROM events WHERE source_text = %s"


def nova_transacao(marca: str):
    return add_transaction.invoke({
        "amount": 1.0, "source_text": marca, "description": marca, "type_name": "EXPENSES",
    })


def novo_evento(marca: str):
    return add_event.invoke({
        "title": marca, "source_text": marca, "start_time": "2030-01-01T10:00:00-03:00",
    })


# add_transaction
m = f"ZZ-{RODADA}-1"
turno(lambda: nova_transacao(m), autorizado=False)
conferir("add_transaction recusado", contar(TX, m), 0)
m = f"ZZ-{RODADA}-2"
turno(lambda: nova_transacao(m), autorizado=True)
conferir("add_transaction aprovado", contar(TX, m), 1)

# add_event
m = f"ZZ-{RODADA}-3"
turno(lambda: novo_evento(m), autorizado=False)
conferir("add_event recusado", contar(EV, m), 0)
m = f"ZZ-{RODADA}-4"
turno(lambda: novo_evento(m), autorizado=True)
conferir("add_event aprovado", contar(EV, m), 1)

# update_transaction (altera a linha aprovada acima, a descrição é o marcador novo)
base = f"ZZ-{RODADA}-5"
id_base = turno(lambda: nova_transacao(base), autorizado=True)["id"]
novo = f"ZZ-{RODADA}-6"
turno(lambda: update_transaction.invoke({"id": id_base, "description": novo}), autorizado=False)
conferir("update_transaction recusado", contar(TX_DESC, novo), 0)
turno(lambda: update_transaction.invoke({"id": id_base, "description": novo}), autorizado=True)
conferir("update_transaction aprovado", contar(TX_DESC, novo), 1)

# duas escritas no mesmo turno
m = f"ZZ-{RODADA}-7"
abrir_turno_db()
nova_transacao(m)
nova_transacao(m)  # a segunda escrita do turno usa a mesma conexão
encerrar_turno_db(False)
conferir("duas escritas no mesmo turno, turno recusado", contar(TX, m), 0)

# quatro add_transaction simultâneas no mesmo turno (o ToolNode roda tool calls em paralelo)
perdas = 0
for rodada in range(12):
    m = f"ZZ-{RODADA}-p{rodada}"
    abrir_turno_db()
    with ThreadPoolExecutor(4) as pool:
        futuros = [pool.submit(contextvars.copy_context().run, nova_transacao, f"{m}-{i}") for i in range(4)]
        [f.result() for f in futuros]
    encerrar_turno_db(True)
    perdas += contar("SELECT count(*) FROM transactions WHERE source_text LIKE %s", f"{m}-%") != 4
conferir("4 tools em paralelo: turnos com linha faltando (de 12)", perdas, 0)

# Google Agenda: efeito fora do Postgres só acontece depois do COMMIT aprovado
import app.tools.agenda as agenda  # noqa: E402

chamadas_google = []
agenda._rodar = lambda args: chamadas_google.append(args["title"]) or {"status": "ok"}  # Google falso


def novo_google(titulo: str):
    return agenda.add_google_event.invoke({
        "title": titulo, "source_text": titulo, "start_time": "2030-01-01T10:00:00-03:00",
    })


m = f"ZZ-{RODADA}-g1"
resposta = turno(lambda: novo_google(m), autorizado=False)
conferir("add_google_event recusado: chamadas ao Google", chamadas_google.count(m), 0)
conferir("add_google_event no turno devolve pendente", resposta["status"], "pendente")
m = f"ZZ-{RODADA}-g2"
turno(lambda: novo_google(m), autorizado=True)
conferir("add_google_event aprovado: chamadas ao Google", chamadas_google.count(m), 1)
m = f"ZZ-{RODADA}-g3"
novo_google(m)
conferir("add_google_event fora de turno: cria na hora", chamadas_google.count(m), 1)

print("\nRESULTADO:", "tudo certo" if not falhas else f"falhou: {falhas}")
sys.exit(1 if falhas else 0)
