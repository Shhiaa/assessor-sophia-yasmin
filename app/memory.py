from typing import Optional
import uuid
from datetime import datetime, timezone
from app.llms import llm_rapido
from app.prompts import _PROMPT_RESUMO
from app.db import get_mongo_conn, get_qdrant_conn, gerar_embedding, COLLECTION_MEMORIA
from qdrant_client import models

# ==============================================================================
# CONEXÃO
# ==============================================================================

_mongo      = get_mongo_conn()
db          = _mongo["assessor"]
col_sessoes = db["sessoes"]

try:
    col_sessoes.create_index("user_id")
    col_sessoes.create_index("iniciada_em")
except Exception as exc:
    # Se o MongoDB estiver indisponível na inicialização, não derruba o app
    # inteiro (financeiro/agenda/faq não dependem dele para subir). Os índices
    # são criados de forma idempotente, então basta reiniciar o processo
    # quando a conexão voltar.
    print(f"[memory] Aviso: não foi possível garantir índices no MongoDB ({exc}).")

_sessoes_ativas: dict = {}

def _agora() -> datetime:
    return datetime.now(timezone.utc)

def _formatar_conversa(mensagens: list[dict]) -> str:
    """Formata o array de mensagens em texto para o prompt de resumo."""
    linhas = []
    for msg in mensagens:
        linhas.append(f"{msg['role']}: {msg['content']}")
    return "\n".join(linhas)

def _gerar_resumo(mensagens: list[dict]) -> str:
    """Chama o LLM para gerar o resumo da sessão."""
    conversa = _formatar_conversa(mensagens)
    return llm_rapido.invoke(
        _PROMPT_RESUMO.format(conversa=conversa)
    ).content.strip()

def _doc_id_da_sessao(user_id: str) -> str | None:
    """
    Descobre o documento da sessão EM ANDAMENTO deste usuário, ou None.

    Olha primeiro o cache em memória (_sessoes_ativas). Se não achar, procura
    no MongoDB a sessão mais recente que ainda não foi encerrada — isto é, com
    resumo vazio.

    Essa segunda tentativa existe porque _sessoes_ativas vive na RAM do
    processo: um --reload do uvicorn no meio da conversa esvazia o dicionário.
    Sem ela, iniciar_sessao() criaria um documento novo para a mesma conversa a
    cada reinício, e encerrar_sessao() não acharia nada para resumir — sem erro
    nenhum, apenas silêncio.
    """
    doc_id = _sessoes_ativas.get(user_id)
    if doc_id:
        return doc_id

    doc = col_sessoes.find_one(
        {"user_id": user_id, "resumo": {"$in": ["", None]}},
        {"_id": 1},
        sort=[("iniciada_em", -1)],
    )
    if not doc:
        return None

    _sessoes_ativas[user_id] = doc["_id"]   # repovoa o cache
    return doc["_id"]

# ==============================================================================
# FUNÇÕES
# ==============================================================================
def iniciar_sessao(user_id: str, session_id: Optional[str]) -> None:
    """
    Garante que exista um documento de sessão aberto para este user_id.

    Idempotente: se já existir uma sessão em andamento (aberta, sem resumo)
    para este user_id, não faz nada — reaproveita o documento existente.
    Só cria um documento novo (com _id gerado via uuid4) quando não há
    nenhum ainda aberto.
    """
    if session_id:
        _sessoes_ativas[user_id] = session_id
        return

    if _doc_id_da_sessao(user_id):
        return

    doc_id = str(uuid.uuid4())
    agora  = _agora()

    col_sessoes.insert_one({
        "_id":           doc_id,
        "user_id":       user_id,
        "iniciada_em":   agora,
        "atualizada_em": agora,
        "resumo":        "",
        "mensagens":     [],
    })

    _sessoes_ativas[user_id] = doc_id

def salvar_mensagem(user_id: str, role: str, content: str) -> None:
    """ Adiciona uma mensagem ao array de mensagens da sessão ativa. """
    doc_id = _doc_id_da_sessao(user_id)

    col_sessoes.update_one(
        {"_id": doc_id},
        {
            "$push": {"mensagens": {"role": role, "content": content}},
            "$set":  {"atualizada_em": _agora()},
        },
    )

def encerrar_sessao(user_id) -> str:
    """
    Encerra a sessão ativa:
      1. Carrega mensagens do MongoDB
      2. Gera resumo via LLM
      3. Atualiza documento com resumo e atualizada_em
      4. Remove sessão do estado interno
    Retorna o resumo gerado ou string vazia se não houver mensagens.
    """
    doc_id = _doc_id_da_sessao(user_id)
    qdrant = get_qdrant_conn()

    if not doc_id:
        return ""

    doc = col_sessoes.find_one({"_id": doc_id})

    if not doc or not doc.get("mensagens"):
        _sessoes_ativas.pop(user_id, None)
        return ""

    resumo = _gerar_resumo(doc["mensagens"])

    col_sessoes.update_one(
        {"_id": doc_id},
        {"$set": {"resumo": resumo, "atualizada_em": _agora()}},
    )

    vetor = gerar_embedding(resumo)
    qdrant.upsert(
        collection_name=COLLECTION_MEMORIA,
        points=[
            models.PointStruct(
                id=doc_id,
                vector=vetor,
                payload={
                    "user_id":     user_id,
                    "session_id":  doc_id,
                    "resumo":      resumo,
                    "iniciada_em": doc["iniciada_em"].isoformat(),
                },
            )
        ],
    )

    _sessoes_ativas.pop(user_id)

    return resumo

def recuperar_passadas(user_id: str, limite: int = 3) -> list[dict]:
    """
    Recupera resumos de sessões ANTERIORES (já encerradas) de um usuário.

    Estratégia: olha primeiro os resumos. Se houver termo de busca, filtra
    por ele; senão, traz as sessões mais recentes. As mensagens completas
    NÃO vêm aqui — para isso use recuperar_mensagens(doc_id).

    user_id : identifica o usuário (hoje fixo, depois dinâmico)
    busca   : termo opcional para filtrar resumos relevantes
    limite  : máximo de sessões retornadas (mais recentes primeiro)
    """
    filtro = {"user_id": user_id}

    docs = (
        col_sessoes
        .find(filtro, {"resumo": 1, "iniciada_em": 1})  
        .sort("iniciada_em", -1)                          
        .limit(limite)
    )

    historico = [
        {"doc_id": d["_id"], "iniciada_em": d["iniciada_em"], "resumo": d["resumo"]}
        for d in docs
    ]

    return historico

def recuperar_historico(user_id: str, busca: str = "", limite: int = 3) -> list[dict]:
    if busca:
        qdrant = get_qdrant_conn()
        vetor = gerar_embedding(busca)
        resultados = qdrant.query_points(
            collection_name=COLLECTION_MEMORIA,
            query=vetor,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=user_id),
                    )
                ]
            ),
            limit=limite,
        )

        if resultados.points:
            return [
                {
                    "doc_id":      ponto.id,
                    "iniciada_em": ponto.payload.get("iniciada_em", ""),
                    "resumo":      ponto.payload["resumo"],
                }
                for ponto in resultados.points
            ]

    filtro = {"user_id": user_id, "resumo": {"$nin": ["", None]}}
    docs = (
        col_sessoes
        .find(filtro, {"resumo": 1, "iniciada_em": 1})
        .sort("iniciada_em", -1)
        .limit(limite)
    )

    return [
        {"doc_id": d["_id"], "iniciada_em": d["iniciada_em"], "resumo": d["resumo"]}
        for d in docs
    ]

def recuperar_mensagens(doc_id: str) -> list[dict]:
    """
    Busca o array completo de mensagens de um documento específico, pelo _id.
    Usada no passo 2 — só quando o resumo deu match e você precisa do detalhe
    literal da conversa. No futuro, o doc_id virá do Qdrant.
    """
    doc = col_sessoes.find_one({"_id": doc_id}, {"mensagens": 1})
    return doc["mensagens"] if doc else []

def recuperar_mensagens_ativas(session_id: str) -> list[dict]:
    """
    Recupera as mensagens da sessão ATUALMENTE aberta (ainda não encerrada)
    para este session_id — usada para restaurar o histórico da conversa em
    andamento quando o usuário retorna (ex.: ao recarregar a página).

    Diferente de recuperar_historico(), que busca resumos de sessões
    PASSADAS já encerradas
    """
    doc_id = _doc_id_da_sessao(session_id)
    return recuperar_mensagens(doc_id) if doc_id else []
