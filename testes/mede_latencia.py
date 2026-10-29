"""
Mede o custo da transação por turno, sem LLM e sem juiz: quanto tempo leva uma
escrita no jeito antigo (commit dentro da tool) e no jeito novo (conexão do turno,
COMMIT ou ROLLBACK no fim).

    python testes/mede_latencia.py [N]

O Postgres local não tem latência de rede; num banco remoto cada ida e volta soma.
"""
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools.financeiro import add_transaction  # noqa: E402
from app.unidade_trabalho import abrir_turno_db, encerrar_turno_db  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 100
RODADA = time.strftime("%H%M%S")


def escrever(i: int):
    return add_transaction.invoke({
        "amount": 1.0, "source_text": f"ZZ-lat-{RODADA}-{i}",
        "description": f"ZZ-lat-{RODADA}-{i}", "type_name": "EXPENSES",
    })


def medir(nome: str, uma_vez) -> None:
    tempos = []
    for i in range(N):
        inicio = time.perf_counter()
        uma_vez(i)
        tempos.append((time.perf_counter() - inicio) * 1000)
    tempos.sort()
    print(f"{nome:<44} média {statistics.mean(tempos):6.2f} ms | p50 {tempos[N // 2]:6.2f} ms | "
          f"p95 {tempos[int(N * 0.95) - 1]:6.2f} ms")


def turno(autorizado: bool):
    def uma_vez(i):
        abrir_turno_db()
        escrever(i)
        encerrar_turno_db(autorizado)
    return uma_vez


escrever(-1)  # aquece a conexão global
print(f"{N} escritas de cada tipo (Postgres local)\n")
medir("antes: commit dentro da tool", escrever)
medir("depois: turno aprovado (COMMIT)", turno(True))
medir("depois: turno recusado (ROLLBACK)", turno(False))
