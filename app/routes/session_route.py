"""
As rotas do ciclo de vida da sessão.

Antes: quem encerrava a sessão era o `while True` do script — o usuário digitava
"sair", o laço chamava encerrar_sessao() e imprimia o resumo. Na migração para a
API esse laço sumiu, e com ele o único ponto do sistema que fechava sessões.
Resultado: nenhuma sessão ganhava resumo, e como buscar_historico() só enxerga
sessões COM resumo, a memória de longo prazo ficava permanentemente vazia.

Agora: o navegador decide. O botão "nova sessão" chama POST /sessions/{id}/encerrar
antes de sortear um novo UUID — é o equivalente HTTP de digitar "sair".

Repare que este arquivo, como o chat.py, é fino de propósito: ele recebe o
pedido, chama quem sabe fazer o trabalho (app.memory) e devolve o resultado.
Toda a lógica de resumo mora no memory.py.
"""

from typing import Optional
from fastapi import APIRouter

from app.memory import encerrar_sessao, iniciar_sessao, recuperar_passadas
from app.schemas import SessionResponse

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/{user_id}/iniciar", response_model=SessionResponse)
def iniciar(user_id: str, session_id: Optional[str] = None) -> SessionResponse:
    try:
        iniciar_sessao(user_id, session_id)
    except Exception as exc:
        # MongoDB indisponível: a sessão não fica registrada no banco, mas o
        # chat em si (executar_fluxo_assessor) não depende deste endpoint.
        print(f"[session_route] Aviso: não foi possível iniciar sessão no MongoDB ({exc}).")
    return SessionResponse(session_id=user_id, resumo=None)

@router.post("/{user_id}/encerrar", response_model=SessionResponse)
def encerrar(user_id: str) -> SessionResponse:
    try:
        resumo = encerrar_sessao(user_id)
    except Exception as exc:
        print(f"[session_route] Aviso: não foi possível encerrar sessão no MongoDB ({exc}).")
        resumo = None
    return SessionResponse(session_id=user_id, resumo=resumo or None)

@router.get("/{user_id}/passadas", response_model=list[SessionResponse])
def listar_passadas(user_id: str) -> list[SessionResponse]:
    try:
        passadas = recuperar_passadas(user_id=user_id, limite= 20)
    except Exception as exc:
        print(f"[session_route] Aviso: não foi possível listar sessões passadas ({exc}).")
        return []
    return [
        SessionResponse(session_id=doc.get("doc_id"), resumo=doc.get("resumo"))
        for doc in passadas
    ]
