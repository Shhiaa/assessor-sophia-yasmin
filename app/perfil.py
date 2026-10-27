"""
Perfil financeiro do usuário — dado de apoio consultado pelo especialista
financeiro, não um domínio novo ao lado de finanças/agenda/faq.

Dois tipos de dado, dois bancos:
  - Estruturado (renda, objetivo, tolerância a risco): MongoDB, consulta
    direta por user_id.
  - Texto livre (preferências): Qdrant, indexado frase a frase e recuperado
    por similaridade de embedding — não por palavra-chave, porque uma
    restrição como "não quero nada agressivo" precisa aparecer numa busca
    sobre "investir em cripto" mesmo sem a palavra "cripto" no cadastro.

Só existe escrita aqui (POST /perfil). Não há update/delete parciais nem
leitura via HTTP: o chat e a rota só leem através de buscar_perfil() e
buscar_preferencias_relevantes().
"""
import re
import uuid
from app.db import (
    get_mongo_conn,
    get_qdrant_conn,
    gerar_embedding,
    gerar_embeddings_batch,
    EMBEDDING_DIM,
)
from qdrant_client import models

# Definida aqui (não em app/db.py, para não editar esse arquivo) — mesma
# convenção de nome das outras collections (COLLECTION_MEMORIA, COLLECTION_FAQ).
COLLECTION_PERFIL = "perfil_preferencias"

# ==============================================================================
# CONEXÃO
# ==============================================================================

_mongo      = get_mongo_conn()
db          = _mongo["assessor"]
col_perfis  = db["perfis"]

try:
    col_perfis.create_index("user_id", unique=True)
except Exception as exc:
    # Mesmo racional de app/memory.py: se o MongoDB estiver fora do ar na
    # inicialização, não derruba o app inteiro. O índice é idempotente, então
    # basta reiniciar o processo quando a conexão voltar.
    print(f"[perfil] Aviso: não foi possível garantir índice no MongoDB ({exc}).")

try:
    _qdrant = get_qdrant_conn()
    if not _qdrant.collection_exists(COLLECTION_PERFIL):
        _qdrant.create_collection(
            collection_name=COLLECTION_PERFIL,
            vectors_config=models.VectorParams(size=EMBEDDING_DIM, distance=models.Distance.COSINE),
        )
except Exception as exc:
    # Mesma tolerância: sem Qdrant no ar, o cadastro estruturado (Mongo)
    # ainda funciona; só a busca semântica de preferências fica indisponível
    # até a collection existir.
    print(f"[perfil] Aviso: não foi possível garantir collection no Qdrant ({exc}).")

# ==============================================================================
# HELPERS
# ==============================================================================

def _dividir_preferencias(texto: str) -> list[str]:
    """
    Quebra o texto livre de preferências em frases — uma frase por ponto no
    Qdrant, para a busca semântica discriminar entre elas (ex.: a frase da
    meta de viagem não deveria "empatar" com a frase sobre risco).
    """
    frases = re.split(r"[.;\n]+", texto)
    return [f.strip() for f in frases if f.strip()]

# ==============================================================================
# FUNÇÕES
# ==============================================================================

def salvar_perfil(dados: dict) -> dict:
    """
    Grava o perfil nos dois bancos a partir do mesmo ponto de entrada (a rota
    POST /perfil chama só esta função).

    Upsert por user_id nos dois lugares: salvar de novo ATUALIZA o cadastro
    (Mongo) e substitui as frases indexadas (Qdrant é limpo por user_id antes
    de reinserir) — nunca cria um segundo perfil nem deixa preferência antiga
    sobrevivendo ao lado da nova.
    """
    user_id = dados["user_id"]

    col_perfis.update_one(
        {"user_id": user_id},
        {"$set": dados},
        upsert=True,
    )

    qdrant = get_qdrant_conn()
    qdrant.delete(
        collection_name=COLLECTION_PERFIL,
        points_selector=models.FilterSelector(
            filter=models.Filter(must=[
                models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
            ])
        ),
    )

    frases = _dividir_preferencias(dados["preferencias"])
    if frases:
        vetores = gerar_embeddings_batch(frases)
        pontos = [
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=vetor,
                payload={"user_id": user_id, "texto": frase},
            )
            for vetor, frase in zip(vetores, frases)
        ]
        qdrant.upsert(collection_name=COLLECTION_PERFIL, points=pontos)

    return dados

def buscar_perfil(user_id: str) -> dict | None:
    """Consulta o dado estruturado do perfil. None se o usuário não tiver cadastro."""
    return col_perfis.find_one({"user_id": user_id}, {"_id": 0})

def buscar_preferencias_relevantes(user_id: str, busca: str, limite: int = 3) -> list[str]:
    """
    Busca semântica (embedding + similaridade de cosseno) nas frases de
    preferências deste usuário — nunca de outro: o filtro por user_id no
    Qdrant garante que a busca só enxerga os pontos deste usuário.
    """
    qdrant = get_qdrant_conn()
    vetor = gerar_embedding(busca)
    resultados = qdrant.query_points(
        collection_name=COLLECTION_PERFIL,
        query=vetor,
        query_filter=models.Filter(must=[
            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
        ]),
        limit=limite,
    )
    return [ponto.payload["texto"] for ponto in resultados.points]
