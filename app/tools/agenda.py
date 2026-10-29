import asyncio
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional, List
from langchain.tools import tool
from pydantic import BaseModel, Field

from app.db import get_pgsql_conn as get_conn
from app.config import GOOGLE_OAUTH_CREDENTIALS
from app.unidade_trabalho import serializado, confirmar, desfazer, liberar, depois_do_commit


class AddEventArgs(BaseModel):
    title: str = Field(..., description="Título do evento.")
    source_text: str = Field(..., description="Texto original do usuário.")
    start_time: str = Field(..., description="Início do evento em ISO 8601 (ex.: '2025-06-10T14:00:00-03:00'). Obrigatório.")
    end_time: Optional[str] = Field(
        default=None,
        description="Fim do evento em ISO 8601. Opcional."
    )
    location: Optional[str] = Field(default=None, description="Local do evento (ex.: 'Sala 3', 'Zoom', 'Rua X, 100').")
    notes: Optional[str] = Field(default=None, description="Observações adicionais sobre o evento.")


@tool("add_event", args_schema=AddEventArgs)
@serializado
def add_event(
    title: str,
    source_text: str,
    start_time: str,
    end_time: Optional[str] = None,
    location: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Insere um evento/compromisso na agenda (tabela events) do banco de dados Postgres."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO events
                (title, start_time, end_time, location, notes, source_text)
            VALUES
                (%s, %s::timestamptz, %s::timestamptz, %s, %s, %s)
            RETURNING id, start_time, end_time;
            """,
            (title, start_time, end_time, location, notes, source_text),
        )
        new_id, start, end = cur.fetchone()
        confirmar(conn)
        return {
            "status": "ok",
            "id": new_id,
            "title": title,
            "start_time": str(start),
            "end_time": str(end) if end else None,
        }
    except Exception as e:
        desfazer(conn)
        return {"status": "error", "message": str(e)}
    finally:
        try:
            cur.close()
            liberar(conn)
        except Exception:
            pass


#############
# NOVA TOOL #
#############

class QueryEventsArgs(BaseModel):
    text: Optional[str] = Field(
        default=None,
        description="Texto para busca em title, location, notes OU source_text (ex.: 'dentista', 'Marcos')."
    )
    date_local: Optional[str] = Field(
        default=None,
        description="Data local (YYYY-MM-DD) em America/Sao_Paulo para filtrar o dia."
    )
    date_from_local: Optional[str] = Field(
        default=None,
        description="Data local inicial (YYYY-MM-DD) inclusive."
    )
    date_to_local: Optional[str] = Field(
        default=None,
        description="Data local final (YYYY-MM-DD) inclusive."
    )
    limit: int = Field(default=20, ge=1, le=200, description="Máximo de registros retornados (1..200).")


@tool("query_events", args_schema=QueryEventsArgs)
@serializado
def query_events(
    text: Optional[str] = None,
    date_local: Optional[str] = None,
    date_from_local: Optional[str] = None,
    date_to_local: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """
    Consulta eventos da agenda (tabela events) com filtros por texto e datas locais (America/Sao_Paulo).
    Use esta tool ANTES de confirmar disponibilidade ou conflito.
    Ordenação:
      - Intervalo (date_from_local/date_to_local): ASC (cronológico).
      - Caso contrário: ASC também (próximos compromissos primeiro a partir do filtro).
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        if date_local and (date_from_local or date_to_local):
            return {"status": "error", "message": "Use date_local OU (date_from_local/date_to_local), não ambos."}

        where_sql = "WHERE 1=1"
        params: dict = {"limit": limit}

        if text:
            where_sql += (
                " AND (e.title ILIKE %(text_like)s"
                " OR e.location ILIKE %(text_like)s"
                " OR e.notes ILIKE %(text_like)s"
                " OR e.source_text ILIKE %(text_like)s)"
            )
            params["text_like"] = f"%{text}%"

        if date_local:
            where_sql += (
                " AND e.start_time >= (%(date_local)s::date AT TIME ZONE 'America/Sao_Paulo')"
                " AND e.start_time <  ((%(date_local)s::date + INTERVAL '1 day') AT TIME ZONE 'America/Sao_Paulo')"
            )
            params["date_local"] = date_local
        else:
            if date_from_local:
                where_sql += " AND e.start_time >= (%(date_from)s::date AT TIME ZONE 'America/Sao_Paulo')"
                params["date_from"] = date_from_local
            if date_to_local:
                where_sql += " AND e.start_time < ((%(date_to)s::date + INTERVAL '1 day') AT TIME ZONE 'America/Sao_Paulo')"
                params["date_to"] = date_to_local

        sql = f"""
            SELECT
              e.id,
              e.title,
              e.start_time,
              e.end_time,
              e.location,
              e.notes,
              e.source_text,
              e.recorded_at
            FROM events e
            {where_sql}
            ORDER BY e.start_time ASC
            LIMIT %(limit)s;
        """
        cur.execute(sql, params)
        rows = cur.fetchall()

        results: List[dict] = []
        for r in rows:
            results.append({
                "id": r[0],
                "title": r[1],
                "start_time": str(r[2]),
                "end_time": str(r[3]) if r[3] else None,
                "location": r[4],
                "notes": r[5],
                "source_text": r[6],
                "recorded_at": str(r[7]),
            })

        return {"status": "ok", "count": len(results), "results": results}

    except Exception as e:
        desfazer(conn)
        return {"status": "error", "message": str(e)}
    finally:
        try:
            cur.close()
            liberar(conn)
        except Exception:
            pass


###################
# GOOGLE CALENDAR #
###################
# Efeito FORA do Postgres: o evento é criado no Google pelo servidor MCP
# @cocal/google-calendar-mcp (tool "create-event"), iniciado por stdio com o
# cliente oficial do pacote `mcp`, que já está no requirements.txt.

GOOGLE_MCP_PACOTE = "@cocal/google-calendar-mcp"
GOOGLE_TIMEOUT_S = 20
GOOGLE_FUSO = "America/Sao_Paulo"


class AddGoogleEventArgs(BaseModel):
    title: str = Field(..., description="Título do evento.")
    source_text: str = Field(..., description="Texto original do usuário.")
    start_time: str = Field(..., description="Início em ISO 8601 (ex.: '2025-06-10T14:00:00-03:00'). Obrigatório.")
    end_time: Optional[str] = Field(default=None, description="Fim em ISO 8601. Se ausente, o evento dura 1 hora.")
    location: Optional[str] = Field(default=None, description="Local do evento.")
    notes: Optional[str] = Field(default=None, description="Observações (vai para a descrição do evento).")


def _fim_padrao(start_time: str) -> str:
    return (datetime.fromisoformat(start_time) + timedelta(hours=1)).isoformat()


async def _criar_evento_google(args: dict) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    npx = shutil.which("npx")
    if not npx:
        return {"status": "error", "message": "npx não encontrado: instale o Node.js para usar o Google Agenda."}
    if not GOOGLE_OAUTH_CREDENTIALS:
        return {"status": "error", "message": "GOOGLE_OAUTH_CREDENTIALS não configurada no .env."}

    servidor = StdioServerParameters(
        command=npx,
        args=["-y", GOOGLE_MCP_PACOTE],
        env={**os.environ, "GOOGLE_OAUTH_CREDENTIALS": GOOGLE_OAUTH_CREDENTIALS},
    )
    async with stdio_client(servidor) as (leitura, escrita):
        async with ClientSession(leitura, escrita) as sessao:
            await sessao.initialize()
            ferramentas = await sessao.list_tools()
            criar = next((f for f in ferramentas.tools if f.name == "create-event"), None)
            if criar is None:
                return {"status": "error", "message": "O servidor MCP não expõe a tool create-event."}

            esquema = criar.inputSchema or {}
            propriedades = set(esquema.get("properties", {}))
            candidatos = {
                "calendarId": "primary",
                "summary": args["title"],
                "start": args["start_time"],
                "end": args["end_time"],
                "timeZone": GOOGLE_FUSO,
                "location": args.get("location"),
                "description": args.get("notes"),
            }
            # Só envia o que o esquema real do servidor declara.
            enviar = {k: v for k, v in candidatos.items() if k in propriedades and v is not None}
            faltando = [r for r in esquema.get("required", []) if r not in enviar]
            if faltando:
                return {"status": "error", "message": f"Campos obrigatórios do create-event sem valor: {faltando}"}

            resposta = await sessao.call_tool("create-event", enviar)
            texto = " ".join(c.text for c in resposta.content if getattr(c, "text", None))
            if resposta.isError:
                return {"status": "error", "message": texto or "create-event devolveu erro."}
            return {"status": "ok", "title": args["title"], "start_time": args["start_time"],
                    "end_time": args["end_time"], "google": texto}


def _rodar(args: dict) -> dict:
    async def _com_prazo():
        return await asyncio.wait_for(_criar_evento_google(args), GOOGLE_TIMEOUT_S)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_com_prazo())
    # Já existe um loop nesta thread: roda o novo loop em outra thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_com_prazo())).result()


@tool("add_google_event", args_schema=AddGoogleEventArgs)
def add_google_event(
    title: str,
    source_text: str,
    start_time: str,
    end_time: Optional[str] = None,
    location: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Cria um evento no Google Agenda do usuário (calendário principal).

    No chat o evento só é criado depois que o juiz aprova o turno (resposta "pendente")."""
    try:
        fim = end_time or _fim_padrao(start_time)
        args = {
            "title": title, "start_time": start_time, "end_time": fim,
            "location": location, "notes": notes,
        }
        if depois_do_commit(lambda: _rodar(args)):
            return {"status": "pendente", "title": title, "start_time": start_time, "end_time": fim,
                    "message": "Será criado no Google Agenda depois da confirmação do turno."}
        return _rodar(args)
    except asyncio.TimeoutError:
        return {"status": "error", "message": f"Google Agenda não respondeu em {GOOGLE_TIMEOUT_S}s."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Exporta a lista de tools
TOOLS_AGENDA = [add_event, query_events, add_google_event]
