"""
Ponto de entrada dos servidores MCP do Assessor.AI.

    python -m app.mcp_server.mcp_serve financeiro
    python -m app.mcp_server.mcp_serve faq --transport streamable-http --port 8101
    python -m app.mcp_server.mcp_serve tudo

Um servidor por domínio de tool (financeiro, faq, memoria, perfil) mais o
agregado `tudo`, que registra as oito tools num processo só — útil quando o
cliente prefere uma entrada única a quatro. Os servidores por domínio continuam
existindo porque cada um tem uma dependência diferente: financeiro precisa do
Postgres, faq do Qdrant, memória do Mongo + Qdrant. Separados, a queda de um
banco derruba só o servidor que depende dele.
"""
from app.mcp_server._common import MCPServer, executar
from app.mcp_server import faq_server, financeiro_server, memoria_server, perfil_server

SERVIDORES = {
    "financeiro": financeiro_server.mcp,
    "faq":        faq_server.mcp,
    "memoria":    memoria_server.mcp,
    "perfil":     perfil_server.mcp,
}


def servidor_completo() -> MCPServer:
    """Um servidor único com todas as tools do Assessor.AI."""
    mcp = MCPServer(
        name="assessor",
        version="1.0.0",
        instructions=(
            "Todas as ferramentas do Assessor.AI num servidor só: transações e "
            "saldos (Postgres), FAQ oficial (Qdrant), memória de conversas "
            "anteriores e perfil financeiro cadastrado. As tools de memória e "
            "perfil exigem user_id explícito."
        ),
    )
    for modulo in (financeiro_server, faq_server, memoria_server, perfil_server):
        modulo.registrar(mcp)
    return mcp


def main(argv: list[str] | None = None) -> None:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    alvo = argv[0] if argv and not argv[0].startswith("-") else None
    if alvo is None:
        print(f"Informe o servidor: {', '.join(SERVIDORES)}, tudo", file=sys.stderr)
        raise SystemExit(2)

    resto = argv[1:]
    if alvo == "tudo":
        executar(servidor_completo(), resto)
    elif alvo in SERVIDORES:
        executar(SERVIDORES[alvo], resto)
    else:
        print(f"Servidor desconhecido: {alvo} (use: {', '.join(SERVIDORES)}, tudo)", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
