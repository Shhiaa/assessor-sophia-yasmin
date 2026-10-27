"""
Tool de perfil financeiro — dado de apoio do especialista financeiro para
ancorar conselhos em renda, objetivo, tolerância a risco e preferências
REALMENTE cadastrados, em vez de o modelo chutar.

Uma tool só, não duas: toda recomendação financeira que depende de perfil
precisa do dado estruturado (renda/objetivo/tolerância) E das preferências em
texto livre juntos — nunca de um sem o outro. Duas tools separadas forçariam
o especialista a sempre encadear as duas chamadas para responder qualquer
pergunta desse tipo, sem nenhum ganho: mais uma rodada de tool-calling pelo
mesmo resultado. Não existe tool de escrita aqui — perfil só é gravado pela
rota POST /perfil (tela Perfil); o chat não tem como alterar o cadastro.
"""
from typing import Optional
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from app.perfil import buscar_perfil, buscar_preferencias_relevantes

@tool
def consultar_perfil_financeiro(topico: Optional[str] = None, config: RunnableConfig = None) -> str:
    """Consulta o perfil financeiro cadastrado pelo usuário (renda, objetivo,
    tolerância a risco e preferências) — use SEMPRE antes de recomendar
    quanto guardar, que tipo de investimento combina com o usuário, ou
    qualquer conselho que dependa desses dados. NUNCA invente renda,
    objetivo, tolerância a risco ou preferências: se não houver cadastro,
    esta tool avisa e o usuário deve ser orientado a preencher a tela Perfil.

    Args:
        topico: assunto da pergunta atual do usuário (ex.: "investir em
            cripto"), usado para trazer as preferências cadastradas mais
            relevantes ao tema. Opcional — se ausente, usa o objetivo
            cadastrado como referência.
    """
    configuravel = (config or {}).get("configurable", {})
    user_id      = configuravel.get("user_id") or configuravel.get("thread_id")

    if not user_id:
        return "Não foi possível identificar o usuário para consultar o perfil."

    perfil = buscar_perfil(user_id)
    if not perfil:
        return (
            "Nenhum perfil cadastrado para este usuário. Não invente renda, "
            "objetivo, tolerância a risco ou preferências: oriente a pessoa a "
            "preencher a tela Perfil antes de dar esse tipo de conselho."
        )

    linhas = [
        f"Renda mensal: {perfil['renda_mensal']}",
        f"Objetivo: {perfil['objetivo']}",
        f"Tolerância a risco: {perfil['tolerancia_risco']}",
    ]

    relevantes = buscar_preferencias_relevantes(user_id, topico or perfil["objetivo"])
    if relevantes:
        linhas.append("Preferências relevantes: " + "; ".join(relevantes))

    return "\n".join(linhas)

TOOLS = [consultar_perfil_financeiro]
