import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import logging_config
from app.config import settings
from app.docs import register_openapi_components
from app.schemas import EchoRequest, EchoResponse, HealthResponse
from app.routers.chat import router as chat_router
from app.routers.evaluations import router as evaluations_router
from app.routers.prompts import router as prompts_router
from app.routers.sources import router as sources_router
from app.routers.traces import router as traces_router
from app.services import cache, prompt_db, run_store, trace_db

# Installed before anything else logs, so no module's first line is swallowed.
logging_config.configure()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Prepare the on-disk histories, then hand over to the server.

    Startup is also where a run left over from a previous process is closed
    out: an indexing run lives in memory, so one that was in flight when the
    server stopped did not finish and never will.

    The trace database is prepared here too, which is when unjudged chat traces
    past their retention window are pruned. Judged ones are kept regardless of
    age — someone looked at them, so they are evidence rather than chatter.

    The prompt store is opened last and pruned never: it holds the wording every
    answer is written under, and how many of those are overridden is worth a
    line in the log rather than something you have to open the UI to find out.
    """
    await run_store.initialise()
    await trace_db.initialise()
    await prompt_db.initialise()
    # Deliberately no host or port: uvicorn may have been given different ones
    # on the command line, and a startup line naming the wrong address is worse
    # than one naming none.
    logger.info("%s ready", settings.app_name)
    yield

    # The cache holds a Redis connection when one is configured. Nothing here
    # depends on it surviving, but a connection left open logs a warning on the
    # way out that reads like a fault.
    await cache.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Include routers for modular endpoint registration.
app.include_router(chat_router)
app.include_router(sources_router)
app.include_router(traces_router)
app.include_router(evaluations_router)
app.include_router(prompts_router)

# Register schemas for streamed events, which FastAPI can't infer from routes.
register_openapi_components(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(app=settings.app_name, timestamp=datetime.now(timezone.utc))


@app.post("/echo", response_model=EchoResponse)
def echo(body: EchoRequest) -> EchoResponse:
    return EchoResponse(message=body.message, length=len(body.message))


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
