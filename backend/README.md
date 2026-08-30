# backend

FastAPI + Pydantic HTTP server.

```sh
cd backend
uv sync
uv run python -m app.main        # http://127.0.0.1:8000, docs at /docs
```

Config via env vars prefixed `APP_` (or a `.env` file): `APP_HOST`, `APP_PORT`, `APP_CORS_ORIGINS`.
