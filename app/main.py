from fastapi import FastAPI
from app.routes import routers
from app.config import validar_config, setup_middleware, setup_frontend
from fastapi.staticfiles import StaticFiles

app = FastAPI(
    title = "AssessorIA",
    description = "Assessor financeiro com LangChain e LangGraph",
    version = "0.1.0"
)

setup_middleware(app)

for router in routers:
    app.include_router(router)


@app.get("/health")
def health() -> dict:
    problemas = validar_config()
    return {
        "status": "ok" if not problemas else "atencao",
        "problemas_de_configuracao": problemas,
    }

setup_frontend(app)