"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def load_dotenv(*candidates: Path) -> None:
    """Minimal .env loader: KEY=value lines, real environment variables win."""
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env", _BACKEND_DIR.parent / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./taskflow.db"))
    cors_origins: list[str] = field(
        default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    )
    seed_on_startup: bool = field(default_factory=lambda: _bool("SEED_ON_STARTUP", True))
    # Allows POST /api/demo/reset. Keep off in any shared deployment you care about.
    allow_demo_reset: bool = field(default_factory=lambda: _bool("ALLOW_DEMO_RESET", False))
    frontend_dist: str = field(
        default_factory=lambda: os.getenv("FRONTEND_DIST", str(_BACKEND_DIR.parent / "frontend" / "dist"))
    )

    # AI dependency suggestions. With no key configured, the keyword
    # heuristic is used so the feature still works offline.
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "auto").lower())
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    ai_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("AI_TIMEOUT_SECONDS", "20")))
    ai_rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("AI_RATE_LIMIT_PER_MINUTE", "6")))


@lru_cache
def get_settings() -> Settings:
    return Settings()
