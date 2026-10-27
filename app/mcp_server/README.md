# Servidores MCP do Assessor.AI

Cada tool do agente (`app/tools/*.py`) está exposta também como tool MCP. A
implementação não foi duplicada: o servidor MCP invoca a mesma tool LangChain
que o grafo usa, então corrigir uma regra em `app/tools/` corrige os dois
caminhos de uma vez.

## Servidores e tools

| Servidor | Módulo | Tools | Depende de |
|---|---|---|---|
| `assessor-financeiro` | `financeiro_server.py` | `add_transaction`, `search_transactions`, `saldo_total`, `saldo_diario`, `update_transaction` | Postgres |
| `assessor-faq` | `faq_server.py` | `faq_retriever` | Qdrant + Gemini (embeddings) |
| `assessor-memoria` | `memoria_server.py` | `buscar_historico` | Mongo + Qdrant |
| `assessor-perfil` | `perfil_server.py` | `consultar_perfil_financeiro` | Mongo + Qdrant |
| `assessor` | `mcp_serve.py tudo` | as 8 acima | tudo |

São servidores separados por domínio porque cada um depende de um banco
diferente: com Qdrant fora do ar, o servidor financeiro continua respondendo.
O agregado `tudo` existe para o caso oposto — cliente que prefere uma entrada
única a quatro.

## Executar

```bash
python -m app.mcp_server.mcp_serve financeiro                              # stdio (padrão)
python -m app.mcp_server.mcp_serve tudo
python -m app.mcp_server.mcp_serve faq --transport streamable-http --port 8101
```

O `.env` da raiz é carregado por `app.config`, então o servidor precisa das
mesmas variáveis que a API (`DATABASE_URL`, `MONGO_URI`, `QDRANT_ENDPOINT`,
`QDRANT_API_KEY`, `GEMINI_API_KEY`).

## Configuração do cliente (stdio)

```json
{
  "mcpServers": {
    "assessor-financeiro": {
      "command": "python",
      "args": ["-m", "app.mcp_server.mcp_serve", "financeiro"],
      "cwd": "C:/Users/sophiacastro-ieg/Downloads/assessorv2/assessorv1"
    },
    "assessor-faq": {
      "command": "python",
      "args": ["-m", "app.mcp_server.mcp_serve", "faq"],
      "cwd": "C:/Users/sophiacastro-ieg/Downloads/assessorv2/assessorv1"
    },
    "assessor-memoria": {
      "command": "python",
      "args": ["-m", "app.mcp_server.mcp_serve", "memoria"],
      "cwd": "C:/Users/sophiacastro-ieg/Downloads/assessorv2/assessorv1"
    },
    "assessor-perfil": {
      "command": "python",
      "args": ["-m", "app.mcp_server.mcp_serve", "perfil"],
      "cwd": "C:/Users/sophiacastro-ieg/Downloads/assessorv2/assessorv1"
    }
  }
}
```

Para usar o agregado, troque as quatro entradas por uma só com
`"args": ["-m", "app.mcp_server.mcp_serve", "tudo"]`.

## Duas diferenças em relação às tools do agente

1. **`user_id` explícito.** Em `buscar_historico` e
   `consultar_perfil_financeiro` o LangChain injeta o usuário pelo
   `RunnableConfig`. MCP não tem esse canal, então as tools MCP recebem
   `user_id` como argumento obrigatório e `_common.chamar` remonta o config
   antes de invocar a tool original.
2. **Serialização.** O Postgres devolve `Decimal` e `datetime`; `_common.json_safe`
   converte para `float` e ISO 8601 antes da resposta MCP.

Além disso, `update_transaction` — que existe em `app/tools/financeiro.py` mas
está fora da lista `TOOLS` do agente — entra no servidor MCP: um cliente que
registra transação precisa também de como corrigi-la.
