from typing import Optional, List
from langchain.tools import tool
from pydantic import BaseModel, Field
from datetime import datetime
from app.db import get_pgsql_conn
from app.cambio import MOEDAS, converter_para_brl
from app.unidade_trabalho import confirmar, desfazer

# Auxiliam a descrição dos campos

def _local_date_filter_sql(field: str = "occurred_at") -> str:
    """
    Retorna um trecho SQL para filtragem por dia local em America/Sao_Paulo.
    Ex.: (occurred_at AT TIME ZONE 'America/Sao_Paulo')::date = %s::date
    """
    return f"(({field} AT TIME ZONE 'America/Sao_Paulo')::date = %s::date)"
def _list_categories():
    try:
        conn = get_pgsql_conn()
        cur = conn.cursor()
        sql = """SELECT name FROM categories"""
        cur.execute(sql)
        categories = [row[0] for row in cur.fetchall()]
        return categories
    except Exception as exc:
        # Se o Postgres estiver indisponível na inicialização, não derruba o
        # app inteiro (agenda/faq/memória não dependem dele). As ferramentas
        # de financeiro seguem funcionando; só a lista de categorias fica
        # vazia até o processo ser reiniciado com o banco acessível.
        print(f"[financeiro] Aviso: não foi possível carregar categorias do Postgres ({exc}). "
              "Ferramentas financeiras iniciarão com CATEGORY_TYPES vazio.")
        return []
def _validate_operator(operator: str):
    return OPERATOR_ALIASES.get(operator.strip().upper())

#Procura um id válido conforme o nome do campo (ex types:  1=INCOME, 2=EXPENSES, 3=TRANSFER)
def _resolve_type_id(cur, type_id: Optional[int], type_name: Optional[str]) -> Optional[int]:
    if type_name:
        t = type_name.strip().upper()
        if t in TYPE_ALIASES:
            t = TYPE_ALIASES[t]
        cur.execute("SELECT id FROM transaction_types WHERE UPPER(type)=%s LIMIT 1;", (t,))
        row = cur.fetchone()
        return row[0] if row else None
    if type_id:
        return int(type_id)
    return None
def _resolve_category_id(cur, category_id: Optional[int], category_name: Optional[str]) -> Optional[int]:
    if category_name:
        c = category_name.strip().upper()
        cur.execute("SELECT id FROM categories WHERE UPPER(name)=%s LIMIT 1;", (c,))
        row = cur.fetchone()
        return row[0] if row else None
    if category_id:
        return int(category_id)
    return None


##############################
#     Variáveis Auxiliares   #
##############################

CATEGORY_TYPES = _list_categories()
TYPE_ALIASES = {
    "INCOMES": "INCOME", "ENTRADA":"INCOME", "ENTRADAS":"INCOME", "RECEITA": "INCOME", "RECEITAS": "INCOME", "SALÁRIO": "INCOME",
    "EXPENSE": "EXPENSES", "DESPESA": "EXPENSES", "GASTO": "EXPENSES",
    "TRANSFERS": "TRANSFER", "TRANSFERÊNCIA": "TRANSFER", "TRANSFERENCIA": "TRANSFER"
}
OPERATOR_ALIASES = {
    # LT
    "LT": "<",
    "<": "<",
    "LESS THAN": "<",
    "MENOR QUE": "<",
    "MENOR": "<",

    # LTE
    "LTE": "<=",
    "<=": "<=",
    "LESS THAN OR EQUAL": "<=",
    "LESS THAN OR EQUAL TO": "<=",
    "MENOR OU IGUAL": "<=",
    "MENOR OU IGUAL A": "<=",
    "AT MOST": "<=",

    # GT
    "GT": ">",
    ">": ">",
    "GREATER THAN": ">",
    "MAIOR QUE": ">",
    "MAIOR": ">",

    # GTE
    "GTE": ">=",
    ">=": ">=",
    "GREATER THAN OR EQUAL": ">=",
    "GREATER THAN OR EQUAL TO": ">=",
    "MAIOR OU IGUAL": ">=",
    "MAIOR OU IGUAL A": ">=",
    "AT LEAST": ">=",

    # EQ
    "EQ": "=",
    "=": "=",
    "==": "=",
    "EQUAL": "=",
    "EQUAL TO": "=",
    "EQUALS": "=",
    "IGUAL": "=",
    "IGUAL A": "=",
    "IS": "=",
    "EXACTLY": "=",

    # NE
    "NE": "!=",
    "!=": "!=",
    "<>": "!=",
    "NOT EQUAL": "!=",
    "NOT EQUAL TO": "!=",
    "DIFFERENT": "!=",
    "DIFFERENT FROM": "!=",
    "DIFERENTE": "!=",
    "DIFERENTE DE": "!=",
    "IS NOT": "!=",
    "NOT": "!=",
}

##############################
#      Adicionar Transação   #
##############################

# Essa classe garante que o objeto de Python passe todos esses campos
class AddTransactionArgs(BaseModel):
    amount: float = Field(..., description="Valor da transação, na moeda de 'currency' (use positivo).")
    currency: str = Field(default="BRL", description="Moeda do valor informado pelo usuário: BRL | USD | EUR (dólar = USD, euro = EUR). A tool converte para reais.")
    source_text: str = Field(..., description="Texto original do usuário.")
    description: str = Field(..., description="Descrição simples da transação.")
    type_name: str = Field(..., description="Nome do tipo: INCOME | EXPENSES | TRANSFER.")
    category_name: Optional[str] = Field(
        default="outros", 
        description=f"Categoria da transação; Se ausente, usa 'outros' (opcional) (categorias disponíveis: {CATEGORY_TYPES})."
    )
    occurred_at: Optional[str] = Field(
        default=None,
        description="Timestamp ISO 8601; se ausente, registra a data de hoje no banco (opcional)."
    )
    payment_method: Optional[str] = Field(default=None, description="Forma de pagamento (opcional).")

@tool("add_transaction", args_schema=AddTransactionArgs)
def add_transaction(
    amount: float,
    source_text: str,
    description: str,
    type_name: str,
    category_name: Optional[str] = None,
    payment_method: Optional[str] = None,
    occurred_at: Optional[str] = None,
    category_id: int = 12,
    type_id: int = 2,
    currency: str = "BRL"
) -> dict:
    """Insere uma transação financeira no banco de dados Postgres.""" # docstring obrigatório da @tools do langchain (estranho, mas legal né?)
    moeda = currency.strip().upper()
    if moeda not in MOEDAS:
        return {"status": "error", "message": f"Moeda inválida '{currency}' (use: {', '.join(MOEDAS)})."}
    conversao = converter_para_brl(amount, moeda)
    amount = conversao["valor_brl"]

    conn = get_pgsql_conn()
    cur = conn.cursor()
    try:
        resolved_type_id = _resolve_type_id(cur, type_id, type_name)
        if not resolved_type_id:
            return {"status": "error", "message": "Tipo inválido (use type_name: INCOME/EXPENSES/TRANSFER)."}
        
        resolved_category_id = _resolve_category_id(cur, category_id, category_name)
        if not resolved_category_id:
            return {"status": "error", "message": f"Categoria inválida (use as seguintes categorias: {CATEGORY_TYPES})."}

        if occurred_at:
            cur.execute(
                """
                INSERT INTO transactions
                    (amount, type, category_id, description, payment_method, occurred_at, source_text)
                VALUES
                    (%s, %s, %s, %s, %s, %s::timestamptz, %s)
                RETURNING id, occurred_at;
                """,
                (amount, resolved_type_id, resolved_category_id, description, payment_method, occurred_at, source_text),
            )
        else:
            cur.execute(
                """
                INSERT INTO transactions
                    (amount, type, category_id, description, payment_method, occurred_at, source_text)
                VALUES
                    (%s, %s, %s, %s, %s, NOW(), %s)
                RETURNING id, occurred_at;
                """,
                (amount, resolved_type_id, resolved_category_id, description, payment_method, source_text),
            )

        new_id, occurred = cur.fetchone()
        confirmar(conn)
        return {
            "status": "ok", "id": new_id, "occurred_at": str(occurred),
            "amount_brl": amount, "moeda_original": moeda, "cotacao_brl": conversao["cotacao"],
            "fonte_cotacao": conversao["fonte"],
        }

    except Exception as e:
        desfazer(conn)
        return {"status": "error", "message": str(e)}

##############################
#     Procurar Transações    #
##############################

class SearchTransactionArgs(BaseModel):
    amount_min: Optional[float] = Field(
        default=None,
        description="Valor mínimo do intervalo de valores a buscar (opcional; informe junto com amount_max)."
    )
    amount_max: Optional[float] = Field(
        default=None,
        description="Valor máximo do intervalo de valores a buscar (opcional; informe junto com amount_min)."
    )
    amount_query_value: Optional[float] = Field(
        default=None,
        description="Valor numérico a comparar via amount_query_operator (opcional; informe junto com amount_query_operator)."
    )
    amount_query_operator: Optional[str] = Field(
        default=None,
        description="Operador de comparação aplicado a amount_query_value (opcional) (operacoes_disponíveis: LT | LTE | GT | GTE | EQ | NE)."
    )
    occurred_at_start: Optional[str] = Field(
        default=None,
        description="Início (timestamp ISO 8601, 'dd-mm-yyyy') do intervalo de data a buscar (opcional; informe junto com occurred_at_end)."
    )
    occurred_at_end: Optional[str] = Field(
        default=None,
        description="Fim (timestamp ISO 8601, 'dd-mm-yyyy') do intervalo de data a buscar (opcional; informe junto com occurred_at_start)."
    )
    occurred_query_date: Optional[str] = Field(
        default=None,
        description="Timestamp ISO 8601 ('dd-mm-yyyy') a comparar via occurred_query_operator (opcional; informe junto com occurred_query_operator)."
    )
    occurred_query_operator: Optional[str] = Field(
        default=None,
        description="Operador de comparação aplicado a occurred_query_date (opcional) (operacoes_disponíveis: LT | LTE | GT | GTE | EQ | NE)."
    )
    search_text: Optional[str] = Field(default=None, description="Expressão Regular que deverá ser procurada no texto original passado pelo usuário para registrar a transferência. (opcional)")
    categories_name: Optional[list[str]] = Field(default=None, description=f"Lista com os tipo de categoria da transação procurados (opcional) (categorias disponíveis: {CATEGORY_TYPES}).")
    types_name: Optional[list[str]] = Field(default=None, description="Lista com os tipos de transferência procurados (opcional) (INCOME | EXPENSES | TRANSFER).")
    description: Optional[str] = Field(default=None, description="Expressão Regular que procurará a descrição do gasto (opcional) (ex: Energético, Marmita, MacDonalds).")
    payment_method: Optional[str] = Field(default=None, description="Expressão Regular que procurará a forma de pagamento (opcional).")

@tool("search_transactions", args_schema=SearchTransactionArgs)
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
    payment_method: Optional[str] = None
) -> dict:
    """Consulta transações financeiras no banco de dados Postgres. Todos os filtros são opcionais."""
    conn = get_pgsql_conn()
    cur = conn.cursor()
    try:
        filtros = []
        param = []

        if (amount_min is not None) != (amount_max is not None):
            return {"status": "error", "message": "Informe amount_min e amount_max juntos para buscar por intervalo de valor."}
        if amount_min is not None and amount_max is not None:
            filtros.append("T.amount BETWEEN %s AND %s")
            param.append(amount_min)
            param.append(amount_max)

        if (amount_query_value is not None) != (amount_query_operator is not None):
            return {"status": "error", "message": "Informe amount_query_value e amount_query_operator juntos."}
        if amount_query_value is not None and amount_query_operator is not None:
            op = _validate_operator(amount_query_operator)
            if op is None:
                return {"status": "error", "message": f"Operador inválido '{amount_query_operator}' (use: LT | LTE | GT | GTE | EQ | NE)."}
            filtros.append(f"T.amount {op} %s")
            param.append(amount_query_value)

        if (occurred_at_start is not None) != (occurred_at_end is not None):
            return {"status": "error", "message": "Informe occurred_at_start e occurred_at_end juntos para buscar por intervalo de data."}
        if occurred_at_start is not None and occurred_at_end is not None:
            filtros.append("DATE(T.occurred_at) BETWEEN %s AND %s")
            param.append(occurred_at_start)
            param.append(occurred_at_end)

        if (occurred_query_date is not None) != (occurred_query_operator is not None):
            return {"status": "error", "message": "Informe occurred_query_date e occurred_query_operator juntos."}
        if occurred_query_date is not None and occurred_query_operator is not None:
            op = _validate_operator(occurred_query_operator)
            if op is None:
                return {"status": "error", "message": f"Operador inválido '{occurred_query_operator}' (use: LT | LTE | GT | GTE | EQ | NE)."}
            filtros.append(f"T.occurred_at {op} %s")
            param.append(occurred_query_date)

        if search_text is not None:
            filtros.append("T.source_text ~ %s")
            param.append(search_text)
        if categories_name is not None:
            categories = tuple(_resolve_category_id(cur, None, category_name) for category_name in categories_name)
            filtros.append("T.category_id IN %s")
            param.append(categories)
        if types_name is not None:
            types = tuple(_resolve_type_id(cur, None, type_name) for type_name in types_name)
            filtros.append("T.type IN %s")
            param.append(types)
        if description is not None:
            filtros.append("T.description ~ %s")
            param.append(description)
        if payment_method is not None:
            filtros.append("T.payment_method ~ %s")
            param.append(payment_method)


        sql = """
            SELECT T.id "ID", amount "Valor", Ty.type "Tipo", C.name "Categoria",
            T.occurred_at "Data", T.description "Descrição", T.source_text "Texto do usuário"
            FROM transactions T
            JOIN categories C ON T.category_id = C.id
            JOIN transaction_types Ty ON T.type = Ty.id
            """
        
        if filtros:
            filtro = " WHERE " + " AND ".join(filtros)
            sql+=filtro
            cur.execute(sql, param)
        else:
            cur.execute(sql)

        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        entries = [dict(zip(columns, row)) for row in rows]
        return {"status": "ok", "entries": entries}
    except Exception as e:
        desfazer(conn)
        return {"status": "error", "message": str(e)}

##############################
#         Saldo Atual        #
##############################

@tool("saldo_total")
def saldo_total() -> dict:
    """Consulta o saldo total do usuário.""" # docstring obrigatório da @tools do langchain (estranho, mas legal né?)
    conn = get_pgsql_conn()
    cur = conn.cursor()
    try:
        sql = """SELECT
            COALESCE(SUM(CASE WHEN type = 1 THEN amount ELSE 0 END), 0) -
            COALESCE(SUM(CASE WHEN type = 2 THEN amount ELSE 0 END), 0) "Saldo"
            FROM transactions;
            """
        
        cur.execute(sql)

        balance = cur.fetchone()[0]
        return {"status": "ok", "saldo": balance}
    except Exception as e:
        desfazer(conn)
        return {"status": "error", "message": str(e)}

##############################
#        Saldo Diário        #
##############################
class DailyBalanceArgs(BaseModel):
    occurred_at: Optional[str] = Field(
        default=None,
        description="String no estilo Timestamp ISO 8601; se ausente, usa o dia de hoje."
    )

@tool("saldo_diario", args_schema=DailyBalanceArgs)
def saldo_diario(
    occurred_at: Optional[str] = None
) -> dict:
    """Consulta o saldo diário do usuário no dia especificado.""" # docstring obrigatório da @tools do langchain (estranho, mas legal né?)
    conn = get_pgsql_conn()
    cur = conn.cursor()
    if occurred_at is None:
        occurred_at = datetime.now().strftime("%Y-%m-%d")
    try:
        sql = """SELECT
            COALESCE(SUM(CASE WHEN type = 1 THEN amount ELSE 0 END), 0) -
            COALESCE(SUM(CASE WHEN type = 2 THEN amount ELSE 0 END), 0) "Saldo"
            FROM transactions
            WHERE DATE(occurred_at) = %s;
            """
        
        cur.execute(sql, (occurred_at,))

        balance = cur.fetchone()[0]
        return {"status": "ok", "saldo": balance}
    except Exception as e:
        desfazer(conn)
        return {"status": "error", "message": str(e)}

##############################
#           UPDATE           #
##############################

class UpdateTransactionArgs(BaseModel):
    id: Optional[int] = Field(
        default=None,
        description="ID da transação a atualizar. Se ausente, será feita uma busca por (match_text + date_local)."
    )
    match_text: Optional[str] = Field(
        default=None,
        description="Texto para localizar transação quando id não for informado (busca em source_text/description)."
    )
    date_local: Optional[str] = Field(
        default=None,
        description="Data local (YYYY-MM-DD) em America/Sao_Paulo; usado em conjunto com match_text quando id ausente."
    )
    amount: Optional[float] = Field(default=None, description="Novo valor.")
    type_id: Optional[int] = Field(default=None, description="Novo type_id (1/2/3).")
    type_name: Optional[str] = Field(default=None, description="Novo type_name: INCOME | EXPENSES | TRANSFER.")
    category_id: Optional[int] = Field(default=None, description="Nova categoria (id).")
    category_name: Optional[str] = Field(default=None, description="Nova categoria (nome).")
    description: Optional[str] = Field(default=None, description="Nova descrição.")
    payment_method: Optional[str] = Field(default=None, description="Novo meio de pagamento.")
    occurred_at: Optional[str] = Field(default=None, description="Novo timestamp ISO 8601.")

@tool("update_transaction", args_schema=UpdateTransactionArgs)
def update_transaction(
    id: Optional[int] = None,
    match_text: Optional[str] = None,
    date_local: Optional[str] = None,
    amount: Optional[float] = None,
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
    category_id: Optional[int] = None,
    category_name: Optional[str] = None,
    description: Optional[str] = None,
    payment_method: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> dict:
    """
    Atualiza uma transação existente.
    Estratégias:
      - Se 'id' for informado: atualiza diretamente por ID.
      - Caso contrário: localiza a transação mais recente que combine (match_text em source_text/description)
        E (date_local em America/Sao_Paulo), então atualiza.
    Retorna: status, rows_affected, id, e o registro atualizado.
    """
    if not any([amount, type_id, type_name, category_id, category_name, description, payment_method, occurred_at]):
        return {"status": "error", "message": "Nada para atualizar: forneça pelo menos um campo (amount, type, category, description, payment_method, occurred_at)."}

    conn = get_pgsql_conn()
    cur = conn.cursor()
    try:
        # Resolve target_id
        target_id = id
        if target_id is None:
            if not match_text or not date_local:
                return {"status": "error", "message": "Sem 'id': informe match_text E date_local para localizar o registro."}

            # Buscar o mais recente no dia local informado que combine o texto
            cur.execute(
                f"""
                SELECT t.id
                FROM transactions t
                WHERE (t.source_text ILIKE %s OR t.description ILIKE %s)
                AND {_local_date_filter_sql("t.occurred_at")}
                ORDER BY t.occurred_at DESC
                LIMIT 1;
                """,
                (f"%{match_text}%", f"%{match_text}%", date_local)
            )
            row = cur.fetchone()
            if not row:
                return {"status": "error", "message": "Nenhuma transação encontrada para os filtros fornecidos."}
            target_id = row[0]

        # Resolver type_id / category_id a partir de nomes, se fornecidos
        resolved_type_id = _resolve_type_id(cur, type_id, type_name) if (type_id or type_name) else None
        resolved_category_id = category_id
        if category_name and not category_id:
            resolved_category_id = _resolve_category_id(cur, category_name)

        # Montar SET dinâmico
        sets = []
        params: List[object] = []
        if amount is not None:
            sets.append("amount = %s")
            params.append(amount)
        if resolved_type_id is not None:
            sets.append("type = %s")
            params.append(resolved_type_id)
        if resolved_category_id is not None:
            sets.append("category_id = %s")
            params.append(resolved_category_id)
        if description is not None:
            sets.append("description = %s")
            params.append(description)
        if payment_method is not None:
            sets.append("payment_method = %s")
            params.append(payment_method)
        if occurred_at is not None:
            sets.append("occurred_at = %s::timestamptz")
            params.append(occurred_at)

        if not sets:
            return {"status": "error", "message": "Nenhum campo válido para atualizar."}

        params.append(target_id)

        cur.execute(
            f"UPDATE transactions SET {', '.join(sets)} WHERE id = %s;",
            params
        )
        rows_affected = cur.rowcount
        confirmar(conn)

        # Retornar o registro atualizado
        cur.execute(
            """
            SELECT
              t.id, t.occurred_at, t.amount, tt.type AS type_name,
              c.name AS category_name, t.description, t.payment_method, t.source_text
            FROM transactions t
            JOIN transaction_types tt ON tt.id = t.type
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.id = %s;
            """,
            (target_id,)
        )
        r = cur.fetchone()
        updated = None
        if r:
            updated = {
                "id": r[0],
                "occurred_at": str(r[1]),
                "amount": float(r[2]),
                "type": r[3],
                "category": r[4],
                "description": r[5],
                "payment_method": r[6],
                "source_text": r[7],
            }

        return {
            "status": "ok",
            "rows_affected": rows_affected,
            "id": target_id,
            "updated": updated
        }

    except Exception as e:
        desfazer(conn)
        return {"status": "error", "message": str(e)}

# Exporta a lista de tools
TOOLS = [add_transaction, search_transactions, saldo_total, saldo_diario, update_transaction]