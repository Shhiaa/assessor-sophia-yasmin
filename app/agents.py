from app.llms import llm_especialista, llm_rapido
from langchain.agents import create_agent
from app.prompts import (
    ROUTER_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    AGENDA_PROMPT_COMPLETO,
    FAQ_PROMPT,
)
from app.tools.financeiro import TOOLS as financeiro_tools
from app.tools.faq import TOOLS as faq_tools
from app.tools.memory import TOOLS as memory_tools
from app.tools.perfil import TOOLS as perfil_tools
from app.tools.agenda import TOOLS_AGENDA as agenda_tools

router_app       = create_agent(model=llm_rapido,       system_prompt=ROUTER_PROMPT_COMPLETO,       tools=memory_tools)
agenda_app       = create_agent(model=llm_especialista, system_prompt=AGENDA_PROMPT_COMPLETO,       tools=agenda_tools + memory_tools)
financeiro_app   = create_agent(model=llm_especialista, system_prompt=FINANCEIRO_PROMPT_COMPLETO,   tools=financeiro_tools + memory_tools + perfil_tools)
orquestrador_app = create_agent(model=llm_rapido,       system_prompt=ORQUESTRADOR_PROMPT_COMPLETO)
faq_app          = create_agent(model=llm_rapido,       system_prompt=FAQ_PROMPT,                   tools=faq_tools)