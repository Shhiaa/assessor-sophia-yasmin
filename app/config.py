import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ==============================================================================
# CONSTANTES
# ==============================================================================

BASE_DIR     = Path(__file__).resolve().parent.parent
DATA_DIR     = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR / "frontend"
FAQ_PDF_PATH = DATA_DIR / "FAQ_assessor_v1.1.pdf"
load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
QDRANT_API_KEY  = os.getenv("QDRANT_API_KEY")
QDRANT_ENDPOINT = os.getenv("QDRANT_ENDPOINT")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY")
MONGODB_URI     = os.getenv("MONGO_URI")
FRONTEND_DIR    = BASE_DIR / "frontend"
URL_DB          = os.getenv("DATABASE_URL")
TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY")
# Caminho do gcp-oauth.keys.json usado pelo servidor MCP do Google Calendar.
# Fora de OBRIGATORIAS: sem ele só add_google_event deixa de funcionar.
GOOGLE_OAUTH_CREDENTIALS = os.getenv("GOOGLE_OAUTH_CREDENTIALS")

# Cotação de reserva (BRL por 1 unidade) usada só se a API de câmbio falhar.
COTACAO_RESERVA = {
    "USD": float(os.getenv("USD_BRL", "5.50")),
    "EUR": float(os.getenv("EUR_BRL", "6.00")),
}

OBRIGATORIAS = {
    "GEMINI_API_KEY": GEMINI_API_KEY,
    "GROQ_API_KEY":   GROQ_API_KEY,
    "MONGODB_URI":    MONGODB_URI,
}

# ==============================================================================
# MÉTODOS
# ==============================================================================

def setup_middleware(app):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"]
    )

def setup_frontend(app):
    if (FRONTEND_DIR / "index.html").exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    else:
        @app.get("/")
        async def root():
            return {
                "message": "Frontend not found. Please build the frontend and place it in the 'frontend' directory."
            }

def validar_config() -> list[str]:
    """Devolve a lista de problemas de configuração (vazia = tudo certo)."""
    problemas = []
    for nome, valor in OBRIGATORIAS.items():
        if not valor:
            problemas.append(f"Variável ausente no .env: {nome}")
    if not FAQ_PDF_PATH.exists():
        problemas.append(f"PDF do FAQ não encontrado em: {FAQ_PDF_PATH}")
    return problemas