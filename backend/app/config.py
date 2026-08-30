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

    # Cloudflare R2 (S3-compatible) — the source-of-truth store for the files
    # we embed.  Credentials come straight from env, like the other providers.
    # Account-scoped, not R2-specific — Workers, D1 and KV share this value.
    cloudflare_account_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CLOUDFLARE_ACCOUNT_ID", "R2_ACCOUNT_ID", "cloudflare_account_id"
        ),
    )
    r2_access_key_id: str = Field(
        default="",
        validation_alias=AliasChoices("R2_ACCESS_KEY_ID", "r2_access_key_id"),
    )
    r2_secret_access_key: str = Field(
        default="",
        validation_alias=AliasChoices("R2_SECRET_ACCESS_KEY", "r2_secret_access_key"),
    )
    r2_bucket: str = Field(
        default="00-ai",
        validation_alias=AliasChoices("R2_BUCKET", "r2_bucket"),
    )

    # Largest file accepted through the upload endpoints.  Bounded because an
    # oversized document is an embedding bill, not just a large object.
    max_upload_bytes: int = 10 * 1024 * 1024

    @property
    def r2_endpoint_url(self) -> str:
        """S3-compatible endpoint for this account's R2 storage."""
        return f"https://{self.cloudflare_account_id}.r2.cloudflarestorage.com"

    @property
    def r2_configured(self) -> bool:
        """Whether enough credentials are present to talk to R2."""
        return bool(
            self.cloudflare_account_id
            and self.r2_access_key_id
            and self.r2_secret_access_key
        )

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
