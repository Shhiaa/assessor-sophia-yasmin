"""
Reproduz o desencontro "o chat disse não, o banco disse sim" nas três tools
de escrita: add_transaction, update_transaction e add_event.

Rode da raiz do projeto, com o .env preenchido (DATABASE_URL) e, para a
camada 2, com o uvicorn no ar:

    python testes/reproduz_desencontro.py > saida_antes.md

Camada 1 (sem LLM, sem juiz): chama a tool e, logo em seguida, consulta o
banco por uma CONEXÃO SEPARADA. Se a linha já aparece, o commit aconteceu
dentro da tool, antes de qualquer veredito. É a prova de que o juiz não tem
como desfazer a escrita no desenho atual.

Camada 2 (chat de ponta a ponta): manda a frase ao /chat e compara a
resposta com o estado do banco. O JEV não é determinístico, então a recusa
pode não acontecer; o script só relata o que viu.

Cada tentativa usa um marcador único (ZZ-<hora>-<n>).
"""
import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402

from app.config import URL_DB  # noqa: E402

FRASE_SEGURA = "Não tenho evidência para afirmar isso."
RODADA = time.strftime("%H%M%S")


def marcador(n: int) -> str:
    return f"ZZ-{RODADA}-{n}"


def contar(tabela: str, marca: str) -> int:
    """Conta linhas com o marcador, numa conexão nova (enxerga só o que foi confirmado)."""
    conn = psycopg2.connect(URL_DB)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) FROM {tabela} WHERE source_text ILIKE %s", (f"%{marca}%",))
        return cur.fetchone()[0]
    finally:
        conn.close()


def valor(marca: str) -> float | None:
    conn = psycopg2.connect(URL_DB)
    try:
        cur = conn.cursor()
        cur.execute("SELECT amount FROM transactions WHERE source_text ILIKE %s ORDER BY id DESC LIMIT 1", (f"%{marca}%",))
        linha = cur.fetchone()
        return float(linha[0]) if linha else None
    finally:
        conn.close()


def camada1() -> list[tuple]:
    from app.tools.agenda import add_event
    from app.tools.financeiro import add_transaction, update_transaction

    linhas = []

    m1 = marcador(1)
    antes = contar("transactions", m1)
    r = add_transaction.invoke({
        "amount": 30, "source_text": f"Comprei {m1} por 30 dólares",
        "description": m1, "type_name": "EXPENSES", "category_name": "outros",
    })
    depois = contar("transactions", m1)
    linhas.append(("add_transaction", m1, antes, depois, r.get("status")))

    m2 = marcador(2)
    r = add_transaction.invoke({
        "amount": 30, "source_text": f"Comprei {m2} por 30 reais no débito",
        "description": m2, "type_name": "EXPENSES", "category_name": "outros",
    })
    antes_v = valor(m2)
    u = update_transaction.invoke({"id": r["id"], "amount": 50})
    depois_v = valor(m2)
    linhas.append(("update_transaction", m2, f"amount={antes_v}", f"amount={depois_v}", u.get("status")))

    m3 = marcador(3)
    inicio = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    antes = contar("events", m3)
    r = add_event.invoke({"title": m3, "source_text": f"Marque {m3} amanhã", "start_time": inicio})
    depois = contar("events", m3)
    linhas.append(("add_event", m3, antes, depois, r.get("status")))
    return linhas


def chat(base: str, user_id: str, mensagem: str) -> str:
    corpo = json.dumps({"message": mensagem}).encode()
    pedido = urllib.request.Request(
        f"{base}/chat?user_id={user_id}", data=corpo,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(pedido, timeout=120) as resp:
        return json.loads(resp.read().decode())["response"]


def camada2(base: str) -> list[tuple]:
    linhas = []
    usuario = f"zz-repro-{RODADA}"

    m1 = marcador(11)
    antes = contar("transactions", m1)
    resposta = chat(base, usuario, f"Comprei {m1} por 30 dólares hoje.")
    linhas.append(("add_transaction", m1, antes, contar("transactions", m1), resposta))

    m2 = marcador(12)
    antes = contar("events", m2)
    resposta = chat(base, usuario, f"Marque o compromisso {m2} amanhã às 10h.")
    linhas.append(("add_event", m2, antes, contar("events", m2), resposta))
    return linhas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--sem-chat", action="store_true", help="roda só a camada 1")
    args = ap.parse_args()

    print("# Reprodução do desencontro (baseline)\n")
    print(f"Rodada `{RODADA}`.\n")
    print("## Camada 1: a tool confirma antes de qualquer veredito\n")
    print("| Tool | Marcador | Antes | Depois (conexão separada) | Retorno da tool |")
    print("| --- | --- | --- | --- | --- |")
    for tool, marca, antes, depois, status in camada1():
        print(f"| {tool} | {marca} | {antes} | {depois} | {status} |")

    if args.sem_chat:
        return
    print("\n## Camada 2: chat contra banco\n")
    print("| Tool | Marcador | Linhas antes | Linhas depois | Resposta do chat | Recusada pelo juiz? |")
    print("| --- | --- | --- | --- | --- | --- |")
    try:
        for tool, marca, antes, depois, resposta in camada2(args.base_url):
            recusada = "sim" if resposta.strip() == FRASE_SEGURA else "não"
            print(f"| {tool} | {marca} | {antes} | {depois} | {resposta[:80]!r} | {recusada} |")
    except OSError as exc:
        print(f"\nCamada 2 não rodou: {exc}. Suba o uvicorn e tente de novo.")


if __name__ == "__main__":
    main()
