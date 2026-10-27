"""
Rota do cadastro de perfil financeiro.

Só existe escrita: a tela Perfil monta o JSON do contrato e chama POST /perfil.
Não há leitura, listagem nem exclusão via HTTP — quem lê o perfil é o
especialista financeiro, por tool (app/tools/perfil.py).

O contrato (PerfilRequest/PerfilResponse) é definido aqui, e não em
app/schemas.py, para não editar esse arquivo original: tolerancia_risco fora
da lista ou renda_mensal <= 0 são recusados com 422 automaticamente pelo
FastAPI/Pydantic — nunca um erro interno.

Fino de propósito, como chat_route.py e session_route.py: valida e delega a
gravação para app.perfil.
"""
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.perfil import salvar_perfil

router = APIRouter(prefix="/perfil", tags=["perfil"])

class PerfilRequest(BaseModel):
    user_id:          str = Field(..., min_length=1, description="Identificador estável do usuário, o mesmo que o chat usa.")
    renda_mensal:     float = Field(..., gt=0, description="Renda mensal; precisa ser maior que zero.")
    objetivo:         str = Field(..., min_length=1, description="Objetivo financeiro, em frase curta.")
    tolerancia_risco: Literal["baixa", "media", "alta"]
    preferencias:     str = Field(..., min_length=1, description="Texto livre com preferências e restrições do usuário.")

class PerfilResponse(PerfilRequest):
    pass

@router.post("", response_model=PerfilResponse)
def salvar(perfil: PerfilRequest) -> PerfilResponse:
    salvar_perfil(perfil.model_dump())
    return PerfilResponse(**perfil.model_dump())
