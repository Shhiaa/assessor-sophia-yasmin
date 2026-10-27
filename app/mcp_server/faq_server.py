"""
Servidor MCP `assessor-faq` — expõe a tool de app/tools/faq.py.

Busca semântica no FAQ oficial indexado no Qdrant (collection faq_chunks).
"""
from app.mcp_server._common import LEITURA, MCPServer, chamar, executar
from app.tools.faq import faq_retriever as _faq_retriever

INSTRUCOES = (
    "Busca no FAQ oficial do Assessor.AI. Use para perguntas sobre o produto, "
    "suas regras e seu funcionamento — não para dados financeiros do usuário, "
    "que ficam no servidor assessor-financeiro."
)


def registrar(mcp: MCPServer) -> MCPServer:
    """Registra a tool de FAQ no servidor MCP informado."""

    @mcp.tool(name="faq_retriever", annotations=LEITURA)
    def faq_retriever(question: str) -> str:
        """Busca no FAQ oficial os trechos mais relevantes para a pergunta.

        Args:
            question: pergunta do usuário, em linguagem natural.
        """
        return chamar(_faq_retriever, {"question": question})

    return mcp


mcp = registrar(MCPServer(
    name="assessor-faq",
    version="1.0.0",
    instructions=INSTRUCOES,
))

if __name__ == "__main__":
    executar(mcp)
