"""OpenAPI documentation objects, kept out of the route handlers.

Also owns registration of schemas that FastAPI cannot discover on its own —
payloads that are streamed rather than returned as a response body.
"""

from typing import Any

from fastapi import FastAPI

from app.docs.chat import CHAT_COMPONENT_SCHEMAS, CHAT_DESCRIPTION, CHAT_RESPONSES
from app.docs.evaluations import EVALUATIONS_TAG
from app.docs.prompts import PROMPTS_TAG
from app.docs.sources import (
    DEINDEX_DESCRIPTION,
    GET_SOURCE_DESCRIPTION,
    GET_SOURCE_RESPONSES,
    INDEX_SOURCES_DESCRIPTION,
    INDEX_SOURCES_RESPONSES,
    LIST_SOURCES_DESCRIPTION,
    SOURCES_COMPONENT_SCHEMAS,
    SOURCES_TAG,
)
from app.docs.traces import TRACES_TAG

# Schemas referenced by hand-written response docs. FastAPI never sees these on
# a route signature, so without this they resolve to nothing in the docs UI.
_EXTRA_COMPONENT_SCHEMAS: dict[str, Any] = {
    **CHAT_COMPONENT_SCHEMAS,
    **SOURCES_COMPONENT_SCHEMAS,
}


def register_openapi_components(app: FastAPI) -> None:
    """Merge streamed-event schemas into the app's `components.schemas`.

    Wraps the app's own generator rather than rebuilding the document, so
    FastAPI keeps ownership of everything else — and its caching still holds.
    """
    generate = app.openapi

    def openapi_with_stream_schemas() -> dict[str, Any]:
        schema = generate()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        # setdefault: never clobber a schema FastAPI generated for the same model.
        for name, definition in _EXTRA_COMPONENT_SCHEMAS.items():
            components.setdefault(name, definition)
        return schema

    app.openapi = openapi_with_stream_schemas


__all__ = [
    "CHAT_DESCRIPTION",
    "CHAT_RESPONSES",
    "EVALUATIONS_TAG",
    "PROMPTS_TAG",
    "DEINDEX_DESCRIPTION",
    "GET_SOURCE_DESCRIPTION",
    "GET_SOURCE_RESPONSES",
    "INDEX_SOURCES_DESCRIPTION",
    "INDEX_SOURCES_RESPONSES",
    "LIST_SOURCES_DESCRIPTION",
    "SOURCES_TAG",
    "TRACES_TAG",
    "register_openapi_components",
]
