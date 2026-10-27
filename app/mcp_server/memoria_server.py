"""
Servidor MCP `assessor-memoria` — expõe a tool de app/tools/memory.py.

Diferença em relação à tool LangChain: lá o usuário chega pelo RunnableConfig;
aqui `user_id` é argumento explícito, porque o MCP não tem esse canal de
injeção. O config é remontado em _common.chamar antes de invocar a tool
original, então a regra de busca continua sendo a mesma.
"""
from app.mcp_server._common import LEITURA, MCPServer, chamar, executar
from app.tools.memory import buscar_historico as _buscar_historico

INSTRUCOES = (
    "Memória de longo prazo do Assessor.AI: resumos de conversas já encerradas. "
    "Use só quando a resposta depender de algo dito numa conversa passada — "
    "preferências, decisões, planos. Gastos, saldos e eventos vêm das tools "
    "do servidor assessor-financeiro, não daqui. Toda chamada exige user_id."
)


def registrar(mcp: MCPServer) -> MCPServer:
    """Registra a tool de memória no servidor MCP informado."""

    @mcp.tool(name="buscar_historico", annotations=LEITURA)
    def buscar_historico(user_id: str, busca: str) -> str:
        """Consulta conversas ANTERIORES do usuário (sessões já encerradas).

        Args:
            user_id: identificador do usuário dono do histórico.
            busca: assunto a procurar nos resumos das conversas anteriores.
        """
        return chamar(_buscar_historico, {"busca": busca}, user_id=user_id)

    return mcp


mcp = registrar(MCPServer(
    name="assessor-memoria",
    version="1.0.0",
    instructions=INSTRUCOES,
))

if __name__ == "__main__":
    executar(mcp)
