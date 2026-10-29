"""
O caso do HTML: compra em dólares. A tool grava amount=2 sem moeda, o chat
responde em reais, o juiz recusa (moeda trocada). Antes da correção a linha
ficava no banco; agora o rollback a desfaz.

    python testes/prova_dolar.py [--modelo especialista|rapido]

Mostra a mensagem enviada, a resposta, o veredito do juiz e as linhas do banco
com o marcador ZZ-<hora em letras>-<letra do caso>. Caso a: dólar (esperado:
recusa, 0 linhas). Caso b: reais (esperado: aprova, 1 linha).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402

import app.llms as llms  # noqa: E402
import app.tools.memory as memoria  # noqa: E402

# Sem cota no Gemini e sem Qdrant neste ambiente (ver MUDANCAS.md).
llms.llm_especialista = llms.llm_rapido if "rapido" in sys.argv else llms.llm_groq
memoria.recuperar_historico = lambda *a, **k: []

from app.config import URL_DB  # noqa: E402
from app.graph import executar_fluxo_assessor  # noqa: E402

RODADA = time.strftime("%H%M%S").translate(str.maketrans("0123456789", "abcdefghij"))


def linhas(marca: str) -> str:
    conn = psycopg2.connect(URL_DB)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, amount, description FROM transactions WHERE description LIKE %s OR source_text LIKE %s",
                        (f"%{marca}%", f"%{marca}%"))
            achadas = cur.fetchall()
            cur.execute("SELECT last_value FROM transactions_id_seq")
            return f"linhas={achadas} seq_id={cur.fetchone()[0]}"
    finally:
        conn.close()


for letra, frase in (
    ("a", "Comprei uma coca por 2 dólares agora, descrição {m}, registre como despesa."),
    ("b", "Gastei 2 reais com uma coca agora, descrição {m}, registre como despesa."),
):
    marca = f"ZZ-{RODADA}-{letra}"
    pedido = frase.format(m=marca)
    print(f"\n=== caso {letra}\nENVIADO : {pedido}\nANTES   : {linhas(marca)}")
    # O gpt-oss do Groq às vezes monta a tool call inválida (400); tenta de novo em outra sessão.
    for tentativa in range(1, 6):
        try:
            print("RETORNO :", executar_fluxo_assessor(pedido, f"dolar-{RODADA}-{letra}-{tentativa}"))
            break
        except Exception as exc:
            print(f"tentativa {tentativa}: exceção {type(exc).__name__}; banco: {linhas(marca)}")
    print(f"DEPOIS  : {linhas(marca)}")
