from pathlib import Path
from urllib.parse import quote

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

    # How many files may be waiting to be embedded at once.  One worker drains
    # the queue, so this bounds the backlog a few impatient clicks can build —
    # every entry is an embedding bill waiting to be paid.
    max_index_queue: int = 50

    # --- Caching ------------------------------------------------------------
    # The /sources reads join object storage against the vector index, and the
    # index side is the expensive half: finding orphans means walking every
    # vector id, and describing each file means another round trip per file.
    # None of that changes between two page loads, so it is cached.
    cache_enabled: bool = True

    # Redis connection, given either as a whole URL or as its parts.  The parts
    # are the better form for a password: a URL has to percent-encode anything
    # in `@:/?#%`, and a generated password will contain some of it sooner or
    # later.  `redis_dsn` below settles which one is in force.
    redis_url: str = Field(
        default="",
        validation_alias=AliasChoices("REDIS_URL", "redis_url"),
    )
    redis_host: str = Field(
        default="",
        validation_alias=AliasChoices("REDIS_HOST", "redis_host"),
    )
    redis_port: int = Field(
        default=6379,
        validation_alias=AliasChoices("REDIS_PORT", "redis_port"),
    )
    redis_username: str = Field(
        default="",
        validation_alias=AliasChoices("REDIS_USERNAME", "redis_username"),
    )
    redis_password: str = Field(
        default="",
        validation_alias=AliasChoices("REDIS_PASSWORD", "redis_password"),
    )
    redis_db: int = Field(
        default=0,
        validation_alias=AliasChoices("REDIS_DB", "redis_db"),
    )
    # Whether to reach Redis over TLS. Managed providers generally require it;
    # a Redis on localhost generally does not offer it.
    redis_tls: bool = Field(
        default=False,
        validation_alias=AliasChoices("REDIS_TLS", "redis_tls"),
    )

    # How long a cached read may stand before it is rebuilt regardless of
    # whether anything looks changed.  This is the backstop for the one case
    # the freshness checks cannot see: an edit made directly on the R2 or
    # Pinecone console that leaves the vector count identical.  Short, because
    # the whole point is that the cached data is cheap to rebuild.
    cache_ttl_seconds: int = 60

    # Whether the prompt overrides are held in memory between reads.  Separate
    # from `cache_enabled` because it is a different decision on different
    # evidence: that one guards network calls to R2 and Pinecone, this one
    # guards a four-row read of a local SQLite file, and it is safe only
    # because nothing but this process writes that file.  Turn it off the
    # moment there is a second worker — each would hold its own copy and never
    # hear about the other's edit.
    prompt_cache_enabled: bool = True

    # Where run history and logs are written.  Relative to the backend package's
    # parent, so it resolves the same however uvicorn was launched.
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"

    @property
    def run_history_path(self) -> Path:
        """SQLite file holding the history of indexing runs."""
        return self.data_dir / "runs.db"

    @property
    def trace_history_path(self) -> Path:
        """SQLite file holding chat traces and the evaluations made on them.

        Deliberately a different file from `run_history_path`: run history is
        pruned on a fixed schedule, and an evaluated trace has to outlive that.
        """
        return self.data_dir / "traces.db"

    @property
    def prompt_store_path(self) -> Path:
        """SQLite file holding edits made to the pipeline's prompts.

        A third file rather than a table in either of the others, because its
        retention rule is "never".  Runs prune at thirty days and unjudged
        traces with them; the wording that decides how every answer is grounded
        must not share a database with anything that deletes on a timer.
        """
        return self.data_dir / "prompts.db"

    @property
    def log_path(self) -> Path:
        """Rotating log file for the backend."""
        return self.data_dir / "logs" / "backend.log"

    @property
    def redis_dsn(self) -> str:
        """The Redis connection string, however it was configured.

        An explicit `REDIS_URL` wins outright — someone who supplied a whole
        URL means it. Otherwise one is assembled from the parts, which is the
        form that does not make the operator think about percent-encoding: the
        credentials are quoted here instead, so a password full of punctuation
        connects rather than failing to parse.

        Returns:
            A connection string, or empty when no Redis is configured — which
            is what puts the cache on its in-process backend.
        """
        if self.redis_url:
            return self.redis_url

        if not self.redis_host:
            return ""

        # `safe=""` so nothing at all is left unescaped; a password containing
        # `@` or `/` would otherwise be read as part of the host or the path.
        credentials = ""
        if self.redis_password or self.redis_username:
            credentials = (
                f"{quote(self.redis_username, safe='')}:"
                f"{quote(self.redis_password, safe='')}@"
            )

        scheme = "rediss" if self.redis_tls else "redis"

        return f"{scheme}://{credentials}{self.redis_host}:{self.redis_port}/{self.redis_db}"

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

    # Workspace an identity-linked Anthropic key acts in.  Keys issued to a
    # person rather than a workspace are rejected without it, so Claude stays
    # unavailable until this is set.  Organisation-level keys ignore it.
    anthropic_workspace_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ANTHROPIC_WORKSPACE_ID", "anthropic_workspace_id"
        ),
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
