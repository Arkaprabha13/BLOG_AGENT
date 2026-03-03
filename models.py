"""
models.py — Pydantic Models (request / response / internal state)
Closed-Loop Autonomous Blog Empire
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class BlogStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    OPTIMIZED = "optimized"
    FAILED = "failed"


class ContentNodeType(str, Enum):
    ROOT = "root"
    SECTION = "section"
    CLAIM = "claim"


# ---------------------------------------------------------------------------
# Published Blog
# ---------------------------------------------------------------------------

class BlogBase(BaseModel):
    slug: str = Field(..., description="URL-safe unique identifier")
    topic: str = Field(..., min_length=3, max_length=512)
    niche: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=512)
    markdown_content: str = Field(..., min_length=10)
    teaser: str = Field(default="", max_length=600)
    tags: list[str] = Field(default_factory=list)

    @field_validator("slug")
    @classmethod
    def slug_must_be_url_safe(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v):
            raise ValueError(
                "slug must be lowercase alphanumeric with hyphens only, e.g. 'my-blog-post'"
            )
        return v


class BlogCreate(BlogBase):
    """Used when saving a freshly generated post."""
    main_url: str = Field(default="")
    devto_url: str = Field(default="")
    hashnode_url: str = Field(default="")
    status: BlogStatus = BlogStatus.DRAFT


class BlogRead(BlogBase):
    """Returned by API and Telegram /stats."""
    id: int
    main_url: str
    devto_url: str
    hashnode_url: str
    status: BlogStatus
    publish_date: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BlogSummary(BaseModel):
    """Lightweight view for listing / stats."""
    id: int
    slug: str
    title: str
    topic: str
    niche: str
    status: BlogStatus
    publish_date: datetime
    views: int = 0
    seo_score: float = 0.0

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

class FixRecord(BaseModel):
    ts: datetime
    note: str


class AnalyticsEntry(BaseModel):
    blog_id: int
    views: int = Field(default=0, ge=0)
    seo_score: float = Field(default=0.0, ge=0.0, le=100.0)
    last_optimized: datetime | None = None
    fix_history: list[FixRecord] = Field(default_factory=list)
    recorded_at: datetime | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Content Tree (PageIndex RAG)
# ---------------------------------------------------------------------------

class ContentNode(BaseModel):
    id: int | None = None
    blog_id: int | None = None
    node_type: ContentNodeType
    node_key: str
    content: str
    parent_id: int | None = None
    verified: bool = False
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# LangGraph State Schemas
# ---------------------------------------------------------------------------

class GenerationState(BaseModel):
    """Shared state passed between LangGraph nodes in System 1."""
    topic: str
    niche: str

    # Populated by Scout Node
    raw_context: str = ""

    # Populated by Writer Node
    draft_markdown: str = ""
    draft_title: str = ""
    draft_tags: list[str] = Field(default_factory=list)

    # Populated by Revisor Node
    revision_notes: str = ""
    hallucination_detected: bool = False
    revision_count: int = 0
    max_revisions: int = Field(default=3)

    # Populated by Publisher Node
    blog_id: int | None = None
    slug: str = ""
    main_url: str = ""
    devto_url: str = ""
    hashnode_url: str = ""
    publish_success: bool = False
    error_message: str = ""

    @model_validator(mode="after")
    def clamp_revisions(self) -> GenerationState:
        if self.revision_count > self.max_revisions:
            self.revision_count = self.max_revisions
        return self


class OptimizationState(BaseModel):
    """Shared state passed between LangGraph nodes in System 2."""

    # Populated by Ingestion Node
    low_performing_blogs: list[dict[str, Any]] = Field(default_factory=list)

    # Populated by Diagnostic Node
    current_blog: dict[str, Any] = Field(default_factory=dict)
    diagnosis: str = ""
    suggested_fixes: list[str] = Field(default_factory=list)

    # Populated by Optimizer Node
    rewritten_title: str = ""
    rewritten_content: str = ""
    rewritten_tags: list[str] = Field(default_factory=list)

    # Populated by Update Node
    update_success: bool = False
    blogs_processed: int = 0
    error_message: str = ""


# ---------------------------------------------------------------------------
# Telegram Bot Payloads
# ---------------------------------------------------------------------------

class GenerateCommand(BaseModel):
    chat_id: int
    topic: str
    niche: str = "general"


class StatsResponse(BaseModel):
    blogs: list[BlogSummary]
    total_blogs: int
    total_views: int

    @property
    def avg_seo(self) -> float:
        if not self.blogs:
            return 0.0
        return round(sum(b.seo_score for b in self.blogs) / len(self.blogs), 2)


# ---------------------------------------------------------------------------
# API Request / Response Helpers
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=512)
    niche: str = Field(default="general", max_length=256)


class OptimizeRequest(BaseModel):
    threshold_views: int = Field(default=100, ge=0, description="Blogs below this view count are targeted")
    threshold_seo: float = Field(default=50.0, ge=0.0, le=100.0)


class APIResponse(BaseModel):
    success: bool
    message: str
    data: Any = None
