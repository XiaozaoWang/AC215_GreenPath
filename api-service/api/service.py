# The front door of FastAPI: build the app, turn on CORS, attach each group of endpoints. What uvicorn starts is the app here.
# When uvicorn looks for the app object, it runs this whole python file
from contextlib import asynccontextmanager
from fastapi import FastAPI
from api import pipeline
from starlette.middleware.cors import CORSMiddleware

from api.routers import score, source

# Build the app (the title and description shown on the /docs page)
@asynccontextmanager
async def lifespan(app):
    pipeline.capabilities()  # fail startup if the selected artifact is missing/corrupt
    yield


app = FastAPI(lifespan=lifespan, title="SmartPath API", description="Scenery preference parsing + tile scoring", version="v1")

# CORS: let pages on other ports (frontend localhost:3000) call this API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


#Health check: open localhost:9000/ and you'll see this sentence, which means the service is alive
@app.get("/")
def get_index():
    return {"message": "Welcome to the SmartPath API Service"}


#Attach the scoring endpoints (/preference, /score)
app.include_router(score.router)

#Attach the code viewer endpoint (/source), used by the page to show the backend code
app.include_router(source.router)


@app.get("/capabilities")
def get_capabilities():
    return pipeline.capabilities()
