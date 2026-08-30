from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.docs import register_openapi_components
from app.schemas import EchoRequest, EchoResponse, HealthResponse
from app.routers.chat import router as chat_router
from app.routers.sources import router as sources_router

app = FastAPI(title=settings.app_name)

# Include routers for modular endpoint registration.
app.include_router(chat_router)
app.include_router(sources_router)

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
