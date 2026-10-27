"""
Infra compartilhada pelos servidores MCP do Assessor.AI.

Cada tool do agente (app/tools/*.py) é exposta também como tool MCP, para que
clientes externos (Claude Desktop, Claude Code, outro agente) usem exatamente a
mesma implementação que o grafo usa — sem reescrever regra de negócio.

Duas diferenças inevitáveis entre a tool LangChain e a tool MCP:

1. `user_id`. Nas tools de memória e perfil o LangChain injeta o usuário via
   `RunnableConfig` (config["configurable"]["user_id"]). MCP não tem esse canal,
   então a tool MCP recebe `user_id` como argumento explícito e o repassa no
   config ao invocar a tool original.
2. Serialização. O Postgres devolve `Decimal`/`datetime`; o MCP devolve JSON.
   `json_safe` converte antes de sair.
"""
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# app/mcp_server/_common.py -> app/mcp_server -> app -> raiz do projeto.
# A raiz precisa estar no sys.path porque as tools importam `app.*`, e um
# servidor MCP é iniciado pelo cliente (stdio) de um diretório qualquer.
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations       # noqa: E402

LEITURA = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
ESCRITA = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
EDICAO  = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


def json_safe(valor: Any) -> Any:
    """Converte o retorno das tools em algo serializável em JSON."""
    if isinstance(valor, dict):
        return {str(k): json_safe(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple, set)):
        return [json_safe(v) for v in valor]
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return valor


def config_do_usuario(user_id: str) -> dict:
    """Monta o RunnableConfig que as tools de memória/perfil esperam."""
    return {"configurable": {"user_id": user_id, "thread_id": user_id}}


def chamar(tool, argumentos: dict, user_id: str | None = None) -> Any:
    """Invoca uma tool LangChain e devolve o resultado pronto para o MCP."""
    if user_id:
        resultado = tool.invoke(argumentos, config=config_do_usuario(user_id))
    else:
        resultado = tool.invoke(argumentos)
    return json_safe(resultado)


def executar(mcp: MCPServer, argv: list[str] | None = None) -> None:
    """Sobe o servidor MCP: stdio por padrão, streamable-http com --transport."""
    import argparse

    parser = argparse.ArgumentParser(description=f"Servidor MCP {mcp.name}")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="stdio (padrão) para clientes locais; streamable-http para rede.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host dos transportes de rede.")
    parser.add_argument("--port", type=int, default=8100, help="Porta dos transportes de rede.")
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run(args.transport, host=args.host, port=args.port)
