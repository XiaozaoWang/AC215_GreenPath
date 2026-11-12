"""FastAPI application: health check, browser access, and API routers."""
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from api.routers import rag, route, score, source

app = FastAPI(title="SmartPath API", description="Scenery preference parsing + tile scoring", version="v1")
# Retain browser access for local development; Cloud Run access is enforced by IAM.
app.add_middleware(CORSMiddleware, allow_credentials=False, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def get_index():
    return {"message": "Welcome to the SmartPath API Service"}


app.include_router(score.router)
app.include_router(source.router)
app.include_router(rag.router)
app.include_router(route.router)
