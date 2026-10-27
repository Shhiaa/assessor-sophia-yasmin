"""
Servidor MCP `assessor-financeiro` — expõe as tools de app/tools/financeiro.py.

Cinco tools, uma por operação do domínio: registrar, consultar, saldo total,
saldo do dia e correção. `update_transaction` existe no módulo de tools mas
está fora da lista TOOLS do agente; no MCP ela entra, porque um cliente externo
que registra transação precisa também de como corrigi-la.
"""
from typing import Optional

from app.mcp_server._common import ESCRITA, LEITURA, EDICAO, MCPServer, chamar, executar
from app.tools.financeiro import (
    CATEGORY_TYPES,
    add_transaction as _add_transaction,
    saldo_diario as _saldo_diario,
    saldo_total as _saldo_total,
    search_transactions as _search_transactions,
    update_transaction as _update_transaction,
)

INSTRUCOES = (
    "Ferramentas financeiras do Assessor.AI sobre o Postgres de transações. "
    f"Categorias cadastradas: {CATEGORY_TYPES}. "
    "Tipos de transação: INCOME | EXPENSES | TRANSFER. "
    "Valores sempre positivos — o tipo é que define entrada ou saída. "
    "Datas em ISO 8601 (YYYY-MM-DD); o dia local considerado é America/Sao_Paulo."
)


def registrar(mcp: MCPServer) -> MCPServer:
    """Registra as tools financeiras no servidor MCP informado."""

    @mcp.tool(name="add_transaction", annotations=ESCRITA)
    def add_transaction(
        amount: float,
        source_text: str,
        description: str,
        type_name: str,
        category_name: Optional[str] = "outros",
        payment_method: Optional[str] = None,
        occurred_at: Optional[str] = None,
    ) -> dict:
        """Insere uma transação financeira no banco.

        Args:
            amount: valor da transação, sempre positivo.
            source_text: texto original do usuário que originou o registro.
            description: descrição curta da transação.
            type_name: INCOME | EXPENSES | TRANSFER.
            category_name: categoria da transação; se ausente, usa 'outros'.
            payment_method: forma de pagamento (opcional).
            occurred_at: timestamp ISO 8601; se ausente, registra agora.
        """
        return chamar(_add_transaction, {
            "amount": amount,
            "source_text": source_text,
            "description": description,
            "type_name": type_name,
            "category_name": category_name,
            "payment_method": payment_method,
            "occurred_at": occurred_at,
        })

    @mcp.tool(name="search_transactions", annotations=LEITURA)
    def search_transactions(
        amount_min: Optional[float] = None,
        amount_max: Optional[float] = None,
        amount_query_value: Optional[float] = None,
        amount_query_operator: Optional[str] = None,
        occurred_at_start: Optional[str] = None,
        occurred_at_end: Optional[str] = None,
        occurred_query_date: Optional[str] = None,
        occurred_query_operator: Optional[str] = None,
        search_text: Optional[str] = None,
        categories_name: Optional[list[str]] = None,
        types_name: Optional[list[str]] = None,
        description: Optional[str] = None,
        payment_method: Optional[str] = None,
    ) -> dict:
        """Consulta transações no banco. Todos os filtros são opcionais.

        Args:
            amount_min: início do intervalo de valor (informe junto com amount_max).
            amount_max: fim do intervalo de valor (informe junto com amount_min).
            amount_query_value: valor comparado via amount_query_operator.
            amount_query_operator: LT | LTE | GT | GTE | EQ | NE.
            occurred_at_start: início do intervalo de data (com occurred_at_end).
            occurred_at_end: fim do intervalo de data (com occurred_at_start).
            occurred_query_date: data comparada via occurred_query_operator.
            occurred_query_operator: LT | LTE | GT | GTE | EQ | NE.
            search_text: regex aplicada ao texto original do usuário.
            categories_name: lista de categorias a filtrar.
            types_name: lista de tipos (INCOME | EXPENSES | TRANSFER).
            description: regex aplicada à descrição.
            payment_method: regex aplicada à forma de pagamento.
        """
        return chamar(_search_transactions, {
            "amount_min": amount_min,
            "amount_max": amount_max,
            "amount_query_value": amount_query_value,
            "amount_query_operator": amount_query_operator,
            "occurred_at_start": occurred_at_start,
            "occurred_at_end": occurred_at_end,
            "occurred_query_date": occurred_query_date,
            "occurred_query_operator": occurred_query_operator,
            "search_text": search_text,
            "categories_name": categories_name,
            "types_name": types_name,
            "description": description,
            "payment_method": payment_method,
        })

    @mcp.tool(name="saldo_total", annotations=LEITURA)
    def saldo_total() -> dict:
        """Consulta o saldo total do usuário (entradas menos saídas)."""
        return chamar(_saldo_total, {})

    @mcp.tool(name="saldo_diario", annotations=LEITURA)
    def saldo_diario(occurred_at: Optional[str] = None) -> dict:
        """Consulta o saldo de um dia específico.

        Args:
            occurred_at: data ISO 8601 (YYYY-MM-DD); se ausente, usa hoje.
        """
        return chamar(_saldo_diario, {"occurred_at": occurred_at})

    @mcp.tool(name="update_transaction", annotations=EDICAO)
    def update_transaction(
        id: Optional[int] = None,
        match_text: Optional[str] = None,
        date_local: Optional[str] = None,
        amount: Optional[float] = None,
        type_name: Optional[str] = None,
        category_name: Optional[str] = None,
        description: Optional[str] = None,
        payment_method: Optional[str] = None,
        occurred_at: Optional[str] = None,
    ) -> dict:
        """Atualiza uma transação existente — por id, ou localizando-a por texto e dia.

        Args:
            id: id da transação; se ausente, informe match_text E date_local.
            match_text: texto procurado em source_text/description.
            date_local: dia local YYYY-MM-DD (America/Sao_Paulo) do registro.
            amount: novo valor.
            type_name: novo tipo (INCOME | EXPENSES | TRANSFER).
            category_name: nova categoria.
            description: nova descrição.
            payment_method: nova forma de pagamento.
            occurred_at: novo timestamp ISO 8601.
        """
        return chamar(_update_transaction, {
            "id": id,
            "match_text": match_text,
            "date_local": date_local,
            "amount": amount,
            "type_name": type_name,
            "category_name": category_name,
            "description": description,
            "payment_method": payment_method,
            "occurred_at": occurred_at,
        })

    return mcp


mcp = registrar(MCPServer(
    name="assessor-financeiro",
    version="1.0.0",
    instructions=INSTRUCOES,
))

if __name__ == "__main__":
    executar(mcp)
