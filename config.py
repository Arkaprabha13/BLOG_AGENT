"""
config.py — Central Settings via pydantic-settings
All secrets are loaded from a .env file (never hard-coded).
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ App
    app_name: str = "Blog Empire"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    base_url: str = "http://localhost:8000"   # Public URL used for canonical links

    # ------------------------------------------------------------------ Groq
    groq_api_key: str = Field(..., description="Groq Cloud API key")
    groq_model: str = "qwen/qwen3-32b"

    # ------------------------------------------------------------------ Telegram
    telegram_bot_token: str = Field(..., description="BotFather token")
    telegram_admin_chat_id: int = Field(..., description="Your personal chat ID for alerts")

    # ------------------------------------------------------------------ Dev.to
    devto_api_key: str = Field(default="", description="Dev.to integration API key")

    # ------------------------------------------------------------------ Hashnode
    hashnode_api_token: str = Field(default="", description="Hashnode Personal Access Token")
    hashnode_publication_id: str = Field(default="", description="Hashnode publication / blog ID")

    # ------------------------------------------------------------------ Database
    database_url: str = Field(
        ...,
        description="PostgreSQL connection URL (from Nhost dashboard → Database → Connection)",
        # Example: postgresql://postgres:PASSWORD@REGION.db.nhost.run:5432/nhost?sslmode=require
    )

    # ------------------------------------------------------------------ SEO / Optimisation
    seo_threshold_views: int = 100         # Below this → considered low traffic
    seo_threshold_score: float = 50.0      # Below this → needs rewrite
    max_revisions: int = 3                 # Max LangGraph writer-revisor loops


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton — import and call this everywhere."""
    return Settings()
