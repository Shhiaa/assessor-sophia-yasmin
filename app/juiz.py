"""
Juiz de fidelidade. Na ordem desta aula ele fala por último.
Quem decide é o JEV: choice e noul, sem redação.
A frase fixa, quando entra, é deste arquivo.
"""
import json
import urllib.error
import urllib.request

from langchain_core.messages import RemoveMessage

from app.config import TYPESAFE_API_KEY

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODELO = "jev-latest"
LIMIAR = 0.70
# A transação do turno fica aberta enquanto o JEV responde (SLO do turno: 8 s).
JEV_TIMEOUT_S = 5
FRASE_SEGURA = "Não tenho evidência para afirmar isso."
# Quando o turno recusado tinha tentado escrever, o usuário precisa saber que nada foi salvo.
FRASE_SEM_ESCRITA = "Não consegui confirmar esse pedido, então nada foi registrado."
TOOLS_DE_ESCRITA = {"add_transaction", "update_transaction", "add_event", "add_google_event"}

REGRAS = (
    "A pergunta do usuário também é evidência do que ele disse que fez. "
    "Somas, contagens, datas convertidas para o horário de Brasília e "
    "reformatação de valores tirados da evidência contam como apoiada. "
    "Esclarecimento, saudação, oferta de ajuda, sugestão de próximo passo "
    "e ausência de registro contam como apoiada. "
    "Opinião do usuário pode ser citada como fala dele. "
    "A moeda de cada valor é a que aparece no source_text ou na fala do usuário. "
    "Moeda trocada, valores de moedas diferentes somados num total só, "
    "opinião dita como fato do sistema, número, data, telefone ou regra "
    "que não saiam da evidência contam como sem_apoio."
)


def _texto(mensagem) -> str:
    conteudo = getattr(mensagem, "content", "")
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        pedacos = []
        for parte in conteudo:
            if isinstance(parte, str):
                pedacos.append(parte)
            elif isinstance(parte, dict):
                pedacos.append(str(parte.get("text") or ""))
        return "\n".join(p for p in pedacos if p)
    return str(conteudo or "")


def _coleta(estado):
    pergunta = ""
    resposta = ""
    evidencias = []
    for mensagem in estado["messages"]:
        tipo = getattr(mensagem, "type", "")
        texto = _texto(mensagem).strip()
        if tipo == "human" and texto:
            pergunta = texto
            evidencias = []
        elif tipo == "tool" and texto:
            nome = getattr(mensagem, "name", "") or "tool"
            evidencias.append(f"[{nome}]\n{texto}")
        elif tipo == "ai":
            for chamada in getattr(mensagem, "tool_calls", None) or []:
                argumentos = json.dumps(chamada.get("args", {}), ensure_ascii=False)
                evidencias.append(f"[chamada {chamada.get('name')}]\n{argumentos}")
            if texto:
                resposta = texto
    evidencia = "\n\n".join(evidencias).strip()
    if not evidencia:
        evidencia = "(nenhuma tool devolveu conteúdo neste turno)"
    return pergunta, resposta, evidencia[-6000:]


def _publicar(texto, rotulo, resposta_original, autorizada=False):
    print(f"[juiz] {rotulo}")
    resultado = {"agentes_chamados": [f"juiz:{rotulo}"], "escrita_autorizada": autorizada}
    if texto.strip() != (resposta_original or "").strip():
        resultado["messages"] = [{"role": "assistant", "content": texto.strip()}]
    return resultado


def _mensagens_do_turno(estado):
    mensagens = estado["messages"]
    inicio = max((i for i, m in enumerate(mensagens) if m.type == "human"), default=0)
    return mensagens[inicio:]


def rastro_do_turno(estado):
    """Remoções das mensagens de IA e de tool do turno: depois de uma recusa (ou de uma
    exceção) o histórico não pode afirmar uma escrita que não permaneceu."""
    return [RemoveMessage(id=m.id) for m in _mensagens_do_turno(estado) if m.type in ("ai", "tool")]


def _tentou_escrever(estado):
    return any(
        chamada.get("name") in TOOLS_DE_ESCRITA
        for m in _mensagens_do_turno(estado) if m.type == "ai"
        for chamada in getattr(m, "tool_calls", None) or []
    )


def _julgar_jev(pergunta, resposta, evidencia):
    if not TYPESAFE_API_KEY:
        return _publicar(FRASE_SEGURA, "sem_chave", resposta)
    corpo = json.dumps({
        "model": JEV_MODELO,
        "state": {
            "pergunta": pergunta,
            "resposta": resposta,
            "evidencia": evidencia,
            "regras": REGRAS,
        },
        "questions": {
            "veredito": {
                "type": "choice",
                "instructions": "Seguindo as regras, a resposta só afirma o que a evidência sustenta?",
                "criteria": {
                    "apoiada": "toda afirmação factual aparece na evidência",
                    "parcial": "parte aparece e parte não",
                    "sem_apoio": "há fato, número, regra ou opinião sem evidência",
                },
            },
            "apoiada": {
                "type": "noul",
                "instructions": (
                    "Seguindo as regras, a resposta é fiel à evidência: todo valor, moeda, "
                    "data e fato dela sai da evidência, da fala do usuário ou de contas sobre elas?"
                ),
            },
        },
    }).encode()
    pedido = urllib.request.Request(
        JEV_URL,
        data=corpo,
        headers={
            "Authorization": f"Bearer {TYPESAFE_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=JEV_TIMEOUT_S) as resp:
            dados = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return _publicar(FRASE_SEGURA, f"erro http {exc.code}", resposta)
    respostas = dados["answers"]
    choice = respostas["veredito"]["choice"]
    noul = float(respostas["apoiada"]["noul"])
    if choice == "apoiada" and noul >= LIMIAR:
        return _publicar(resposta, f"apoiada noul={noul:.2f}", resposta, autorizada=True)
    rotulo = "apoiada:abaixo_do_limiar" if choice == "apoiada" else choice
    return _publicar(FRASE_SEGURA, f"{rotulo} noul={noul:.2f}", resposta)


def no_juiz(estado: dict) -> dict:
    pergunta, resposta, evidencia = _coleta(estado)
    if not resposta:
        resultado = {"agentes_chamados": ["juiz:sem_resposta"]}
    else:
        try:
            resultado = _julgar_jev(pergunta, resposta, evidencia)
        except Exception as exc:
            print(f"[juiz] erro {exc}")
            resultado = _publicar(FRASE_SEGURA, "erro", resposta)
    if not resultado.get("escrita_autorizada"):
        if resposta and _tentou_escrever(estado):
            resultado["messages"] = [{"role": "assistant", "content": FRASE_SEM_ESCRITA}]
        resultado["messages"] = resultado.get("messages", []) + rastro_do_turno(estado)
    return resultado