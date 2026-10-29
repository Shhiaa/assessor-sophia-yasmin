"""
Prova de ponta a ponta pelo grafo (executar_fluxo_assessor), com LLM e juiz reais.

    python testes/prova_chat.py [--sem-juiz]

--sem-juiz zera a chave do JEV: o juiz falha fechado e todo turno é recusado.
Sem a flag, vale o veredito real do JEV.

Para cada pedido mostra a mensagem enviada, a resposta e o que mudou no banco
(contagem de linhas com o marcador ZZ-<hora em letras>-<letra do caso>, lida por conexão separada).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402

import app.juiz as juiz  # noqa: E402
from app.config import URL_DB  # noqa: E402

if "--sem-juiz" in sys.argv:
    juiz.TYPESAFE_API_KEY = ""

import app.llms as llms  # noqa: E402
import app.tools.memory as memoria  # noqa: E402

# A cota diária gratuita do Gemini acabou neste ambiente: o especialista usa o Groq direto.
llms.llm_especialista = llms.llm_groq

# O Qdrant (memória de longo prazo) recusa a conexão neste ambiente; sem ele a tool
# de histórico devolve "nenhuma conversa anterior". Não interfere nas escritas.
memoria.recuperar_historico = lambda *a, **k: []

from app.graph import executar_fluxo_assessor  # noqa: E402

# Letras no lugar de dígitos: o anonimizador do guardrail troca sequências de dígitos por [PII_CONTA_...].
RODADA = time.strftime("%H%M%S").translate(str.maketrans("0123456789", "abcdefghij"))


def linhas(marca: str) -> dict:
    conn = psycopg2.connect(URL_DB)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM transactions WHERE source_text LIKE %s OR description LIKE %s",
                        (f"%{marca}%", f"%{marca}%"))
            tx = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM events WHERE source_text LIKE %s OR title LIKE %s",
                        (f"%{marca}%", f"%{marca}%"))
            ev = cur.fetchone()[0]
            # A sequence não volta no ROLLBACK: se subiu, o INSERT foi tentado.
            cur.execute("SELECT (SELECT last_value FROM transactions_id_seq), (SELECT last_value FROM events_id_seq)")
            seq_tx, seq_ev = cur.fetchone()
            return {"transactions": tx, "events": ev, "seq_tx": seq_tx, "seq_ev": seq_ev}
    finally:
        conn.close()


def caso(n: int, nome: str, frase: str, sessao: str = None) -> None:
    marca = f"ZZ-{RODADA}-{chr(96 + n)}"
    pedido = frase.format(m=marca)
    antes = linhas(marca)
    print(f"\n=== {n}. {nome}\nENVIADO : {pedido}")
    resposta = executar_fluxo_assessor(pedido, sessao or f"teste-{RODADA}-{n}")
    print(f"RETORNO : {resposta}\nBANCO   : antes={antes} depois={linhas(marca)}")


caso(1, "criar transação", "Gastei 12 reais com {m} no almoço de hoje, registre como despesa.")
caso(2, "criar evento", "Registre na agenda o compromisso {m} em 02/01/2030 às 15:00, na sala 3, sem horário de fim.")
caso(3, "consulta (não escreve)", "Qual meu saldo total?")
sessao = f"teste-{RODADA}-4"
caso(4, "criar e depois alterar (mesma sessão)", "Gastei 20 reais com {m} no mercado hoje, despesa.", sessao)
caso(5, "alterar valor", "Corrija o gasto do {m} para 25 reais.", sessao)
