"""
Servidor MCP `assessor-perfil` — expõe a tool de app/tools/perfil.py.

Só leitura, como no agente: perfil é gravado pela rota POST /perfil (tela
Perfil), nunca pelo chat — e, por consequência, nunca por um cliente MCP.
`user_id` é argumento explícito pelo mesmo motivo do servidor de memória.
"""
from typing import Optional

from app.mcp_server._common import LEITURA, MCPServer, chamar, executar
from app.tools.perfil import consultar_perfil_financeiro as _consultar_perfil_financeiro

INSTRUCOES = (
    "Perfil financeiro cadastrado do usuário: renda, objetivo, tolerância a "
    "risco e preferências. Consulte SEMPRE antes de recomendar quanto guardar "
    "ou que investimento combina com a pessoa. Nunca invente esses dados: sem "
    "cadastro, a tool avisa e o usuário deve preencher a tela Perfil. "
    "Toda chamada exige user_id."
)


def registrar(mcp: MCPServer) -> MCPServer:
    """Registra a tool de perfil no servidor MCP informado."""

    @mcp.tool(name="consultar_perfil_financeiro", annotations=LEITURA)
    def consultar_perfil_financeiro(user_id: str, topico: Optional[str] = None) -> str:
        """Consulta o perfil financeiro cadastrado pelo usuário.

        Args:
            user_id: identificador do usuário dono do perfil.
            topico: assunto da pergunta atual (ex.: "investir em cripto"), usado
                para trazer as preferências cadastradas mais relevantes ao tema.
                Opcional — se ausente, usa o objetivo cadastrado.
        """
        return chamar(_consultar_perfil_financeiro, {"topico": topico}, user_id=user_id)

    return mcp


mcp = registrar(MCPServer(
    name="assessor-perfil",
    version="1.0.0",
    instructions=INSTRUCOES,
))

if __name__ == "__main__":
    executar(mcp)
