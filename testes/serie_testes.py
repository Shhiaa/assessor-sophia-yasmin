"""
Série de testes pelo chat completo (grafo, LLM e juiz reais): para cada pedido
mostra a pergunta, a resposta e as linhas que ficaram no banco (marcador em letras).

    python testes/serie_testes.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402

import app.llms as llms  # noqa: E402
import app.tools.memory as memoria  # noqa: E402

# Sem cota no Gemini e sem Qdrant neste ambiente (ver MUDANCAS.md).
llms.llm_especialista = llms.llm_groq
memoria.recuperar_historico = lambda *a, **k: []

from app.config import URL_DB  # noqa: E402
from app.graph import executar_fluxo_assessor  # noqa: E402

RODADA = time.strftime("%H%M%S").translate(str.maketrans("0123456789", "abcdefghij"))


def banco(marca: str) -> str:
    conn = psycopg2.connect(URL_DB)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, amount, description FROM transactions WHERE description LIKE %s OR source_text LIKE %s",
                        (f"%{marca}%", f"%{marca}%"))
            tx = [(i, float(v), d) for i, v, d in cur.fetchall()]
            cur.execute("SELECT id, title, start_time FROM events WHERE title LIKE %s OR source_text LIKE %s",
                        (f"%{marca}%", f"%{marca}%"))
            ev = [(i, t, str(s)[:16]) for i, t, s in cur.fetchall()]
            return f"transactions={tx} events={ev}"
    finally:
        conn.close()


def perguntar(pergunta: str, sessao: str) -> str:
    # O gpt-oss do Groq às vezes monta a tool call inválida (400) ou bate o limite de tokens.
    for tentativa in range(1, 6):
        try:
            return executar_fluxo_assessor(pergunta, sessao)
        except Exception as exc:
            print(f"   (tentativa {tentativa}: {type(exc).__name__}; espera e repete)")
            time.sleep(65)  # o limite da Groq é por minuto
    return "(sem resposta: todas as tentativas falharam)"


def caso(n: int, nome: str, *perguntas: str) -> None:
    marca = f"ZZ-{RODADA}-{chr(96 + n)}"
    sessao = f"serie-{RODADA}-{n}"
    print(f"\n=== {n}. {nome}")
    for p in perguntas:
        p = p.format(m=marca)
        print(f"PERGUNTA: {p}")
        print(f"RESPOSTA: {perguntar(p, sessao)}")
    print(f"BANCO   : {banco(marca)}")


caso(1, "dólar", "Comprei um lanche por 15,50 dólares hoje, descrição {m}, registre como despesa.")
caso(2, "euro", "Paguei 20 euros num jantar hoje, descrição {m}, registre como despesa.")
caso(3, "reais", "Gastei 30 reais com um lanche hoje, descrição {m}, registre como despesa.")
caso(4, "criar e depois corrigir o valor (mesma sessão)",
     "Gastei 20 reais no mercado hoje, descrição {m}, registre como despesa.",
     "Corrija o gasto {m} para 25 reais.")
caso(5, "evento na agenda", "Registre na agenda o compromisso {m} em 02/01/2030 às 15:00, na sala 3, sem horário de fim.")
caso(6, "consulta de saldo (não escreve)", "Qual meu saldo total?")
caso(7, "consulta de agenda (não escreve)", "Quais compromissos eu tenho em 02/01/2030?")
