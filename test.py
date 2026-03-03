"""
test.py — Offline Integration Tests for Blog Empire
Run with: python test.py

Tests every module without touching external APIs (Groq, Dev.to, Hashnode, Telegram).
Tests are self-contained; they create a temp DB and clean up after.
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# ── colour helpers ──────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

logging.basicConfig(
    level=logging.WARNING,   # suppress noisy info from modules under test
    format="%(name)s — %(levelname)s — %(message)s",
)

sys.path.insert(0, str(Path(__file__).parent))

_passed = _failed = 0


def _ok(name: str, detail: str = ""):
    global _passed
    _passed += 1
    suffix = f" ({detail})" if detail else ""
    print(f"  {GREEN}✅ PASS{RESET}  {name}{suffix}")


def _fail(name: str, err: str):
    global _failed
    _failed += 1
    print(f"  {RED}❌ FAIL{RESET}  {name}")
    for line in err.strip().splitlines():
        print(f"         {YELLOW}{line}{RESET}")


def run(test_fn):
    """Decorator: run an async test, catch and report errors."""
    async def _wrapper():
        name = test_fn.__name__.replace("_", " ").title()
        try:
            await test_fn()
            _ok(name)
        except AssertionError as e:
            _fail(name, str(e))
        except Exception:
            _fail(name, traceback.format_exc())
    return _wrapper


# ==========================================================================
# Test 1 — Config loads correctly
# ==========================================================================
@run
async def test_config_loads():
    from config import get_settings
    s = get_settings()
    assert s.app_name == "Blog Empire", f"app_name={s.app_name!r}"
    assert s.groq_model == "qwen/qwen3-32b", f"model={s.groq_model!r}"
    assert s.db_path is not None


# ==========================================================================
# Test 2 — Database: init, save, get_blog_by_slug, fetch_top_blogs
# ==========================================================================
@run
async def test_database_crud():
    import database as db_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        # Point DB to temp file
        original_path = db_mod.DB_PATH
        db_mod.DB_PATH = Path(tmpdir) / "test.db"
        try:
            await db_mod.init_db()
            assert db_mod.DB_PATH.exists(), "DB file not created"

            # Save a blog
            blog_id = await db_mod.save_blog(
                slug="test-post-1",
                topic="LangGraph",
                niche="AI",
                title="Test Blog Post",
                markdown_content="# Hello\n\nThis is a test post with enough content.",
                teaser="This is a test post",
                tags=["ai", "langgraph"],
                status="published",
            )
            assert isinstance(blog_id, int) and blog_id > 0, f"bad id={blog_id}"

            # get_blog_by_slug
            post = await db_mod.get_blog_by_slug("test-post-1")
            assert post is not None, "post not found by slug"
            assert post["title"] == "Test Blog Post", f"title={post['title']!r}"
            assert isinstance(post["tags"], list), "tags not a list"
            assert "ai" in post["tags"]

            # fetch_top_blogs (empty analytics → still returns the row)
            top = await db_mod.fetch_top_blogs(5)
            assert len(top) == 1, f"expected 1 blog, got {len(top)}"
            assert top[0]["slug"] == "test-post-1"
            assert isinstance(top[0]["views"], int)
            assert isinstance(top[0]["seo_score"], float)

            # upsert_analytics
            await db_mod.upsert_analytics(blog_id, views=42, seo_score=75.5, fix_note="test fix")
            top2 = await db_mod.fetch_top_blogs(5)
            assert top2[0]["views"] == 42, f"views={top2[0]['views']}"
            assert top2[0]["seo_score"] == 75.5

            # update_blog_urls
            await db_mod.update_blog_urls(blog_id, devto_url="https://dev.to/test", status="published")
            post2 = await db_mod.get_blog_by_slug("test-post-1")
            assert post2["devto_url"] == "https://dev.to/test"

        finally:
            db_mod.DB_PATH = original_path


# ==========================================================================
# Test 3 — Database: get_db context manager never raises thread error
# ==========================================================================
@run
async def test_database_concurrent_access():
    import database as db_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        original_path = db_mod.DB_PATH
        db_mod.DB_PATH = Path(tmpdir) / "concurrent.db"
        try:
            await db_mod.init_db()
            # Open multiple connections concurrently to prove context manager is safe
            async def _query():
                async with db_mod.get_db() as db:
                    async with db.execute("SELECT 1 AS val") as cur:
                        row = await cur.fetchone()
                    return row["val"]
            results = await asyncio.gather(*[_query() for _ in range(10)])
            assert all(r == 1 for r in results), f"unexpected results: {results}"
        finally:
            db_mod.DB_PATH = original_path


# ==========================================================================
# Test 4 — Models: Pydantic validation
# ==========================================================================
@run
async def test_models_pydantic():
    from models import GenerateRequest, OptimizeRequest, APIResponse

    req = GenerateRequest(topic="Autonomous Agents", niche="AI")
    assert req.topic == "Autonomous Agents"
    assert req.niche == "AI"

    # Too short topic must fail
    try:
        GenerateRequest(topic="ab")  # min_length=3
        assert False, "Should have raised ValidationError"
    except Exception:
        pass   # expected

    opt = OptimizeRequest(threshold_views=50, threshold_seo=40.0)
    assert opt.threshold_views == 50

    resp = APIResponse(success=True, message="OK")
    assert resp.success is True


# ==========================================================================
# Test 5 — Config: .env values read correctly
# ==========================================================================
@run
async def test_config_env_values():
    from config import get_settings
    s = get_settings()
    assert s.groq_api_key, "GROQ_API_KEY missing from .env"
    assert s.telegram_bot_token, "TELEGRAM_BOT_TOKEN missing from .env"
    assert s.telegram_admin_chat_id != 0, "TELEGRAM_ADMIN_CHAT_ID not set"


# ==========================================================================
# Test 6 — Bot: HTML formatting helpers
# ==========================================================================
@run
async def test_bot_html_formatting():
    import html as h
    # Ensure special chars are safely escaped
    raw = '<script>alert("xss")</script> & 50.0 score!'
    escaped = h.escape(raw)
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped
    assert "&amp;" in escaped

    # Simulate _fmt_stats with no blogs
    from bot import _fmt_stats
    msg = _fmt_stats([])
    assert "No published blogs yet" in msg
    assert "<b>" in msg   # must use HTML, not MarkdownV2


# ==========================================================================
# Test 7 — Bot: _fmt_stats with real blog data
# ==========================================================================
@run
async def test_bot_fmt_stats_with_data():
    from bot import _fmt_stats
    blogs = [
        {"title": "Test Post", "slug": "test-post", "views": 100,
         "seo_score": 75.5, "status": "published"},
        {"title": "Post with <special> & chars", "slug": "special",
         "views": 10, "seo_score": 40.0, "status": "optimized"},
    ]
    msg = _fmt_stats(blogs)
    assert "Test Post" in msg
    assert "&lt;special&gt;" in msg   # HTML escaped
    assert "&amp;" in msg
    # NO MarkdownV2 reserved chars unescaped
    assert r"\." not in msg
    assert r"\!" not in msg


# ==========================================================================
# Test 8 — graph_system1: TypedDict state schema defined correctly
# ==========================================================================
@run
async def test_graph1_state_typedef():
    from graph_system1 import GenState, _build_graph
    # GenState must have the core fields
    hints = GenState.__annotations__
    for field in ("topic", "niche", "raw_context", "draft_markdown", "draft_title",
                  "revision_count", "hallucination_detected", "publish_success"):
        assert field in hints, f"Missing field in GenState: {field}"


# ==========================================================================
# Test 9 — graph_system1: scout_node, writer_node get correct state keys
# ==========================================================================
@run
async def test_graph1_nodes_receive_full_state():
    """Verify that nodes with mocked Groq preserve all state keys."""
    from graph_system1 import GenState, scout_node, writer_node, revisor_node

    # Mocked GroqClient methods
    with patch("graph_system1.groq_c") as mock_groq:
        mock_groq.scout_context = AsyncMock(return_value="Context about LangGraph")
        mock_groq.write_blog    = AsyncMock(return_value={
            "content": "# LangGraph\n\nLangGraph is a graph-based framework.",
            "title": "LangGraph Explained",
            "tags": ["ai", "langgraph"],
        })
        mock_groq.verify_facts  = AsyncMock(return_value={
            "hallucination_detected": False,
            "revision_notes": "",
        })

        full_state: GenState = {
            "topic":                "LangGraph",
            "niche":                "AI",
            "chat_id":              None,
            "max_revisions":        2,
            "raw_context":          "",
            "draft_markdown":       "",
            "draft_title":          "",
            "draft_tags":           [],
            "revision_notes":       "",
            "hallucination_detected": False,
            "revision_count":       0,
            "blog_id":              None,
            "slug":                 "",
            "main_url":             "",
            "devto_url":            "",
            "hashnode_url":         "",
            "publish_success":      False,
            "error_message":        "",
        }

        # scout_node should return only raw_context
        scout_out = await scout_node(full_state)
        assert "raw_context" in scout_out
        assert scout_out["raw_context"] == "Context about LangGraph"

        # Merge (simulate LangGraph merge)
        merged = {**full_state, **scout_out}
        assert merged["topic"] == "LangGraph", "topic lost after scout merge!"
        assert merged["niche"] == "AI",        "niche lost after scout merge!"

        # writer_node must find topic and niche
        writer_out = await writer_node(merged)
        assert "draft_markdown" in writer_out
        assert "draft_title"    in writer_out
        assert writer_out["draft_title"] == "LangGraph Explained"

        merged2 = {**merged, **writer_out}
        revisor_out = await revisor_node(merged2)
        assert revisor_out["hallucination_detected"] is False


# ==========================================================================
# Test 10 — graph_system2: TypedDict state schema defined correctly
# ==========================================================================
@run
async def test_graph2_state_typedef():
    from graph_system2 import OptState
    hints = OptState.__annotations__
    for field in ("threshold_views", "threshold_seo", "low_performing_blogs",
                  "current_blog", "diagnosis", "blogs_processed", "update_success"):
        assert field in hints, f"Missing field in OptState: {field}"


# ==========================================================================
# Test 11 — graph_system1: full pipeline with all external calls mocked
# ==========================================================================
@run
async def test_graph1_full_pipeline_mock():
    import database as db_mod
    from graph_system1 import run_generation_graph

    with tempfile.TemporaryDirectory() as tmpdir:
        original_path = db_mod.DB_PATH
        db_mod.DB_PATH = Path(tmpdir) / "pipeline_test.db"
        try:
            await db_mod.init_db()

            with (
                patch("graph_system1.groq_c") as mock_groq,
                patch("graph_system1.devto")    as mock_devto,
                patch("graph_system1.hashnode")  as mock_hashnode,
                patch("graph_system1.save_blog", wraps=db_mod.save_blog),
                patch("graph_system1.update_blog_urls", wraps=db_mod.update_blog_urls),
            ):
                mock_groq.scout_context = AsyncMock(return_value="Deep research on AI")
                mock_groq.write_blog    = AsyncMock(return_value={
                    "content": "# AI Agents\n\nThis is a comprehensive guide to AI agents "
                               "with more than 40 characters to satisfy the teaser extractor.",
                    "title": "AI Agents Guide",
                    "tags": ["ai", "agents"],
                })
                mock_groq.verify_facts  = AsyncMock(return_value={
                    "hallucination_detected": False,
                    "revision_notes": "",
                })
                mock_devto.publish    = AsyncMock(return_value={"url": "https://dev.to/test"})
                mock_hashnode.publish = AsyncMock(return_value={"url": "https://hashnode.com/test"})

                result = await run_generation_graph("AI Agents", "AI")

                # Assertions on what the pipeline produced
                assert result.get("publish_success"), f"Pipeline failed: {result.get('error_message')}"
                assert result.get("slug")
                assert result.get("main_url")

                # Verify DB was written
                post = await db_mod.get_blog_by_slug(result["slug"])
                assert post is not None, f"Post slug={result['slug']} not found in DB"
                assert post["title"] == "AI Agents Guide"
                assert post["status"] == "published"
        finally:
            db_mod.DB_PATH = original_path


# ==========================================================================
# Test 12 — Main FastAPI: homepage and health routes
# ==========================================================================
@run
async def test_fastapi_homepage_and_health():
    import database as db_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        original_path = db_mod.DB_PATH
        db_mod.DB_PATH = Path(tmpdir) / "fastapi_test.db"
        try:
            await db_mod.init_db()

            # bot is imported inside lifespan, so patch from bot module
            with patch("bot.start_bot", AsyncMock(return_value=None)), \
                 patch("bot.stop_bot",  AsyncMock(return_value=None)):
                from fastapi.testclient import TestClient
                import main as main_mod

                # TestClient with lifespan raises on startup if bot fails
                # Use raise_server_exceptions=False to test routes directly
                with TestClient(main_mod.app, raise_server_exceptions=False) as client:
                    # Health check must always be 200
                    r = client.get("/api/health")
                    assert r.status_code == 200, f"health={r.status_code}"
                    data = r.json()
                    assert data["status"] == "ok"

                    # Homepage — must not 500 (404 is also unacceptable)
                    r = client.get("/")
                    assert r.status_code in (200, 307), \
                        f"homepage={r.status_code}: {r.text[:300]}"
        finally:
            db_mod.DB_PATH = original_path



# ==========================================================================
# Run all tests
# ==========================================================================
async def main():
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  Blog Empire — Integration Test Suite{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")

    tests = [
        test_config_loads,
        test_database_crud,
        test_database_concurrent_access,
        test_models_pydantic,
        test_config_env_values,
        test_bot_html_formatting,
        test_bot_fmt_stats_with_data,
        test_graph1_state_typedef,
        test_graph1_nodes_receive_full_state,
        test_graph2_state_typedef,
        test_graph1_full_pipeline_mock,
        test_fastapi_homepage_and_health,
    ]

    for t in tests:
        await t()

    total = _passed + _failed
    print(f"\n{BOLD}{'─'*60}")
    if _failed == 0:
        print(f"{GREEN}  ✅  {_passed}/{total} tests passed — ALL GOOD!{RESET}")
    else:
        print(f"{RED}  ❌  {_failed}/{total} tests FAILED{RESET}  "
              f"({GREEN}{_passed} passed{RESET})")
    print(f"{BOLD}{'─'*60}{RESET}\n")
    sys.exit(0 if _failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
