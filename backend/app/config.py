from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field


class Settings(BaseSettings):
    app_name: str = "00-ai backend"
    host: str = "127.0.0.1"
    port: int = 8000

    # LLM — use validation_alias so pydantic-settings reads the env var
    # directly as OPENAI_API_KEY, bypassing the APP_ prefix.
    openai_api_key: str = Field(
        ...,
        validation_alias=AliasChoices("OPENAI_API_KEY", "openai_api_key"),
    )

    # Pinecone configuration
    pinecone_api_key: str = Field(
        ...,
        validation_alias=AliasChoices("PINECONE_API_KEY", "pinecone_api_key"),
    )
    pinecone_index_name: str = "rag-index"

    # Embedding model — coupled to the *contents* of the Pinecone index, not to
    # the environment.  Chunk vectors and query vectors must come from this same
    # model, so changing it invalidates every stored vector and requires a
    # re-index.  Kept here (not in .env) so that change is visible in a diff.
    embedding_model: str = "text-embedding-3-small"

    # Anthropic (Claude) — same AliasChoices pattern to read ANTHROPIC_API_KEY
    # directly from env, bypassing the APP_ prefix.
    anthropic_api_key: str = Field(
        ...,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key"),
    )

    # Any localhost/127.0.0.1 origin on a 517x port (Vite picks the next free one).
    cors_origin_regex: str = r"^http://(localhost|127\.0\.0\.1):517\d$"
    # Extra exact origins, e.g. a deployed frontend.
    cors_origins: list[str] = []

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")


settings = Settings()
