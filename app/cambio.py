"""
Conversão de moeda para reais: API pública (AwesomeAPI, sem chave) e, se ela
falhar, a cotação de reserva do .env (USD_BRL / EUR_BRL).
"""
import json
import urllib.request

from app.config import COTACAO_RESERVA

MOEDAS = ("BRL", "USD", "EUR")
CAMBIO_URL = "https://economia.awesomeapi.com.br/last/{moeda}-BRL"
CAMBIO_TIMEOUT_S = 3


def converter_para_brl(valor: float, moeda: str) -> dict:
    """Devolve o valor em reais, a cotação usada e a fonte (api | reserva | nenhuma)."""
    if moeda == "BRL":
        return {"valor_brl": round(valor, 2), "cotacao": 1.0, "fonte": "nenhuma"}
    try:
        with urllib.request.urlopen(CAMBIO_URL.format(moeda=moeda), timeout=CAMBIO_TIMEOUT_S) as resp:
            cotacao = float(json.loads(resp.read().decode())[f"{moeda}BRL"]["bid"])
        fonte = "api"
    except Exception:
        cotacao, fonte = COTACAO_RESERVA[moeda], "reserva"
    return {"valor_brl": round(valor * cotacao, 2), "cotacao": cotacao, "fonte": fonte}
