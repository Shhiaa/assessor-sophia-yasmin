// ============================================================
// Configuração
// ============================================================
// Servido pelo próprio FastAPI: usa a mesma origem (qualquer porta). Só cai no
// endereço fixo se a página for aberta direto do disco (file://).
const API_BASE = window.location.protocol === "file:" ? "http://localhost:8001" : "";
const CHAT_ENDPOINT = `${API_BASE}/chat`;

// ============================================================
// Elementos
// ============================================================
const thread = document.getElementById("thread");
const sidebarList = document.querySelector(".sidebar__list");
const threadEmpty = document.getElementById("thread-empty");
const composer = document.getElementById("composer");
const inputPergunta = document.getElementById("pergunta");
const botaoEnviar = document.getElementById("enviar");
const userIdEl = document.getElementById("user-id");
const resetButton = document.getElementById("reset-session");
const statusDot = document.getElementById("status-dot");
const hint = document.getElementById("hint");

// ============================================================
// Sessão
// ============================================================
const USER_STORAGE_KEY = "assistente_user_id";

function gerarUserIdTemporario() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback para contextos sem crypto.randomUUID (ex.: http:// não-localhost).
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// O UUID gerado e persistido no localStorage é a identidade permanente da pessoa;
async function obterUserId() {
  const emCache = localStorage.getItem(USER_STORAGE_KEY);
  if (emCache) return emCache;

  const novo = gerarUserIdTemporario();
  localStorage.setItem(USER_STORAGE_KEY, novo);
  return novo;
}

async function registrarSessaoNoBackend(id, sessionId) {
  try {
    const url = sessionId
      ? `${API_BASE}/sessions/${encodeURIComponent(id)}/iniciar?session_id=${encodeURIComponent(sessionId)}`
      : `${API_BASE}/sessions/${encodeURIComponent(id)}/iniciar`;
    await fetch(url, { method: "POST" });
  } catch (error) {
    console.error("Erro ao iniciar sessão no backend:", error);
  }
}

async function encerrarSessaoNoBackend(id) {
  try {
    await fetch(`${API_BASE}/sessions/${encodeURIComponent(id)}/encerrar`, { method: "POST" });
  } catch (error) {
    console.error("Erro ao encerrar sessão no backend:", error);
  }
}

// Carrega o histórico da conversa em andamento
async function carregarHistorico(id) {
  try {
    const response = await fetch(`${API_BASE}/chat/${encodeURIComponent(id)}`);
    if (!response.ok) {
      throw new Error("Erro ao buscar histórico do servidor");
    }
    const mensagens = await response.json();
    thread.innerHTML = "";
    thread.appendChild(threadEmpty);
    threadEmpty.style.display = mensagens.length ? "none" : "block";
    mensagens.forEach(({ role, content }) => {
      adicionarMensagem({ tipo: role === "human" ? "user" : "assistant", texto: content });
    });
  } catch (error) {
    console.error("Erro ao carregar histórico:", error);
  }
}

async function carregarConversas(id) {
  try {
    const response = await fetch(`${API_BASE}/sessions/${encodeURIComponent(id)}/passadas`);
    if (!response.ok) {
      throw new Error("Erro ao buscar conversas do servidor");
    }
    const conversas = await response.json();
    sidebarList.innerHTML = "";
    conversas.forEach(({ session_id, resumo }) => {
      if (resumo != "")
        adicionarConversa({ session_id, resumo });
    });
  } catch (error) {
    console.error("Erro ao carregar conversas:", error);
  }
}

// "Nova sessão" não troca a identidade (o UUID salvo) — apenas encerra a
// conversa atual (gerando o resumo que alimenta a memória de longo prazo) e
// abre uma conversa nova para a mesma pessoa com um UUID aleatório
async function iniciarNovaSessao() {
  setHint("Encerrando conversa anterior...");
  await encerrarSessaoNoBackend(userId);

  thread.innerHTML = "";
  thread.appendChild(threadEmpty);
  threadEmpty.style.display = "block";

  await registrarSessaoNoBackend(userId);
  await carregarConversas(userId);
  setHint("Nova conversa iniciada.");
}

function exibirUserId(id) {
  userIdEl.textContent = id.length > 12 ? id.slice(0, 8) + "…" : id;
  userIdEl.title = id;
}

let userId = null;

(async function inicializarSessao() {
  userId = await obterUserId();
  exibirUserId(userId);
  await registrarSessaoNoBackend(userId);
  await carregarHistorico(userId);
  await carregarConversas(userId);
})();

resetButton.addEventListener("click", () => {
  iniciarNovaSessao();
});

// ============================================================
// Utilidades de UI
// ============================================================
function setHint(texto, comoErro = false) {
  hint.textContent = texto || "";
  hint.classList.toggle("is-error", comoErro);
}

function marcarStatus(online) {
  statusDot.classList.toggle("is-offline", !online);
}

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto;
  return div.innerHTML;
}

// Converte markdown (respostas do assistente) em HTML seguro.
// O texto vem de um LLM, então sempre sanitizamos antes de injetar no DOM.
function renderizarMarkdown(texto) {
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    return escaparHtml(texto);
  }
  const html = marked.parse(texto, { breaks: true });
  return DOMPurify.sanitize(html);
}

function rolarParaFinal() {
  thread.scrollTop = thread.scrollHeight;
}

function adicionarMensagem({ tipo, texto, agentes }) {
  threadEmpty.style.display = "none";

  const wrapper = document.createElement("div");
  wrapper.className = `message message--${tipo}`;

  const label = document.createElement("div");
  label.className = "message__label";
  label.textContent = tipo === "user" ? "você" : tipo === "error" ? "erro" : "assistente";
  wrapper.appendChild(label);

  const bubble = document.createElement("div");
  bubble.className = "message__bubble message__bubble--md";
  bubble.innerHTML = renderizarMarkdown(texto);
  wrapper.appendChild(bubble);

  if (agentes && agentes.length > 0) {
    const agentesEl = document.createElement("div");
    agentesEl.className = "message__agents";
    agentes.forEach((nome) => {
      const tag = document.createElement("span");
      tag.className = "agent-tag";
      tag.textContent = nome;
      agentesEl.appendChild(tag);
    });
    wrapper.appendChild(agentesEl);
  }

  thread.appendChild(wrapper);
  rolarParaFinal();
  return wrapper;
}

function adicionarConversa({ session_id, resumo }) {
  const wrapper = document.createElement("li");
  wrapper.className = `sidebar__item`;

  const button = document.createElement("button");
  button.className = "sidebar__item-button";
  button.setAttribute("type", "button");
  button.addEventListener("click", () => abrirConversaPassada(session_id));
  wrapper.appendChild(button);

  const id = document.createElement("span");
  id.className = "sidebar__item-id";
  id.textContent = session_id;
  button.appendChild(id);

  const resumo_item = document.createElement("span");
  resumo_item.className = "sidebar__item-resumo";
  resumo_item.textContent = resumo;
  button.appendChild(resumo_item);

  sidebarList.appendChild(wrapper);
}

async function abrirConversaPassada(sessionId) {
  document
    .querySelectorAll(".sidebar__item-button--active")
    .forEach((el) => el.classList.remove("sidebar__item-button--active"));
  const botaoClicado = [...sidebarList.querySelectorAll(".sidebar__item-button")].find(
    (el) => el.querySelector(".sidebar__item-id")?.textContent === sessionId
  );
  botaoClicado?.classList.add("sidebar__item-button--active");

  setHint("Abrindo conversa...");
  await encerrarSessaoNoBackend(userId);
  await registrarSessaoNoBackend(userId, sessionId);

  thread.innerHTML = "";
  thread.appendChild(threadEmpty);
  threadEmpty.style.display = "block";
  await carregarHistorico(userId);
  setHint("");
}

function adicionarIndicadorDigitando() {
  const wrapper = document.createElement("div");
  wrapper.className = "message message--assistant";
  wrapper.id = "typing-indicator";

  const label = document.createElement("div");
  label.className = "message__label";
  label.textContent = "assistente";
  wrapper.appendChild(label);

  const bubble = document.createElement("div");
  bubble.className = "message__bubble";
  bubble.innerHTML = `<span class="typing"><span></span><span></span><span></span></span>`;
  wrapper.appendChild(bubble);

  thread.appendChild(wrapper);
  rolarParaFinal();
}

function removerIndicadorDigitando() {
  const el = document.getElementById("typing-indicator");
  if (el) el.remove();
}

// ============================================================
// Textarea: auto-resize + enviar com Enter
// ============================================================
inputPergunta.addEventListener("input", () => {
  inputPergunta.style.height = "auto";
  inputPergunta.style.height = Math.min(inputPergunta.scrollHeight, 140) + "px";
});

inputPergunta.addEventListener("keydown", (evento) => {
  if (evento.key === "Enter" && !evento.shiftKey) {
    evento.preventDefault();
    composer.requestSubmit();
  }
});

// ============================================================
// Envio da pergunta
// ============================================================
composer.addEventListener("submit", async (evento) => {
  evento.preventDefault();

  const pergunta = inputPergunta.value.trim();
  if (!pergunta) return;

  adicionarMensagem({ tipo: "user", texto: pergunta });
  inputPergunta.value = "";
  inputPergunta.style.height = "auto";
  adicionarIndicadorDigitando();
  botaoEnviar.disabled = true;

  try {
    if (!userId) {
      userId = await obterUserId();
      exibirUserId(userId);
      await registrarSessaoNoBackend(userId);
    }

    // Fetch para o endpoint /chat com user_id e a mensagem no corpo
    const response = await fetch(`${CHAT_ENDPOINT}?user_id=${encodeURIComponent(userId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: pergunta }),
    });

    if (!response.ok) {
      throw new Error(`Erro ${response.status} ao buscar dados do servidor`);
    }

    const data = await response.json();
    removerIndicadorDigitando();
    adicionarMensagem({ tipo: "assistant", texto: data.response });
    marcarStatus(true);
  } catch (error) {
    console.error("Erro na requisição:", error);
    removerIndicadorDigitando();
    adicionarMensagem({ tipo: "error", texto: `Não foi possível obter resposta do assistente. (${error.message})` });
    marcarStatus(false);
  } finally {
    botaoEnviar.disabled = false;
    inputPergunta.focus();
  }
});

// Foco inicial
inputPergunta.focus();
