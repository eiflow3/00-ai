"""Shared / utility schemas that are not specific to a RAG stage."""

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Standard health-check response."""
    status: str = "ok"
    app: str
    timestamp: datetime


class EchoRequest(BaseModel):
    """Simple echo endpoint request body."""
    message: str = Field(min_length=1, max_length=2000)


class EchoResponse(BaseModel):
    """Simple echo endpoint response body."""
    message: str
    length: int
