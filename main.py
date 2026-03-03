"""
main.py — FastAPI application: Web routes + Internal API
Closed-Loop Autonomous Blog Empire
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import markdown as md_lib
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import get_settings
from database import init_db, close_db, get_db, fetch_top_blogs, get_blog_by_slug
from models import GenerateRequest, OptimizeRequest, APIResponse

logger = logging.getLogger(__name__)
settings = get_settings()

# Markdown extensions — codehilite needs pygments; fall back gracefully
_MD_EXTENSIONS_FULL = ["extra", "codehilite", "toc", "nl2br"]
_MD_EXTENSIONS_SAFE = ["extra", "toc", "nl2br"]


def _strip_outer_fence(text: str) -> str:
    """
    Strip an outer ```markdown ... ``` (or ```md / ```) wrapper that LLMs
    sometimes add around their entire response.  Only strips if the VERY
    FIRST non-blank line is a fenced-code opening and the LAST non-blank
    line is the closing ```.
    """
    stripped = text.strip()
    # Check if the whole document is wrapped in a fenced block
    for lang_tag in ("```markdown", "```md", "```"):
        if stripped.startswith(lang_tag) and stripped.endswith("```"):
            inner = stripped[len(lang_tag):].lstrip("\n")
            if inner.endswith("```"):
                inner = inner[:-3].rstrip()
                # Only use the stripped version if it looks like real markdown
                # (i.e. it contains headings or paragraphs, not nested code)
                if inner.strip():
                    return inner
    return text


def render_markdown(text: str) -> str:
    """Convert markdown to HTML. Strips LLM outer fences, falls back gracefully."""
    text = _strip_outer_fence(text)
    try:
        return md_lib.markdown(text, extensions=_MD_EXTENSIONS_FULL)
    except Exception:
        try:
            return md_lib.markdown(text, extensions=_MD_EXTENSIONS_SAFE)
        except Exception:
            return md_lib.markdown(text)


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    )
    logger.info("🚀  Starting Blog Empire …")
    await init_db()

    # Start Telegram bot
    bot_task = None
    try:
        from bot import start_bot
        bot_task = asyncio.create_task(start_bot())
        await asyncio.sleep(0.5)
        logger.info("✅  Telegram bot started")
    except Exception as exc:
        logger.warning("Telegram bot failed to start: %s", exc)

    # Start daily auto-blog scheduler
    try:
        from scheduler import scheduler
        scheduler.start()
        logger.info("✅  Daily scheduler started (next run at 09:00)")
    except Exception as exc:
        logger.warning("Scheduler failed to start: %s", exc)

    yield   # ← FastAPI is live here

    # Shutdown
    try:
        from scheduler import scheduler
        await scheduler.stop()
    except Exception:
        pass

    try:
        await close_db()
    except Exception:
        pass

    if bot_task:
        try:
            from bot import stop_bot
            await stop_bot()
        except Exception:
            pass
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
    logger.info("🛑  Blog Empire stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.app_name,
    description="Closed-Loop Autonomous Blog Empire API",
    version="1.0.0",
    lifespan=lifespan,
)


# Global exception handler — never show raw stack traces to browser
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s", request.url)
    return HTMLResponse(
        content=_error_page(500, "Internal Server Error", str(exc)),
        status_code=500,
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    return HTMLResponse(
        content=_error_page(404, "Page Not Found", f"{request.url} does not exist."),
        status_code=404,
    )


# Static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Templates
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ---------------------------------------------------------------------------
# Web Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def homepage(request: Request):
    """Homepage — lists the latest published blogs."""
    try:
        async with get_db() as conn:
            rows = await conn.fetch(
                """
                SELECT b.id, b.slug, b.title, b.topic, b.niche, b.teaser, b.tags,
                       b.publish_date::TEXT AS publish_date, b.status,
                       COALESCE(a.views, 0)       AS views,
                       COALESCE(a.seo_score, 0.0) AS seo_score
                  FROM published_blogs b
                  LEFT JOIN analytics_log a ON a.blog_id = b.id
                 WHERE b.status IN ('published', 'optimized')
                 ORDER BY b.publish_date DESC
                 LIMIT 20
                """
            )

        blogs = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
            d["seo_score"] = float(d.get("seo_score") or 0.0)
            d["views"]     = int(d.get("views") or 0)
            blogs.append(d)

        return templates.TemplateResponse(
            "index.html",
            {"request": request, "blogs": blogs, "site_name": settings.app_name},
        )
    except Exception as exc:
        logger.exception("Homepage error")
        return HTMLResponse(
            content=_error_page(500, "Homepage Error", str(exc)), status_code=500
        )


@app.get("/blog/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def blog_post(request: Request, slug: str):
    """Renders a single blog post from the database."""
    try:
        post = await get_blog_by_slug(slug)
        if not post:
            return HTMLResponse(
                content=_error_page(404, "Post Not Found", f"No post with slug '{slug}'."),
                status_code=404,
            )

        # Ensure tags is a list
        if not isinstance(post.get("tags"), list):
            try:
                post["tags"] = json.loads(post.get("tags") or "[]")
            except Exception:
                post["tags"] = []

        # Convert markdown → HTML safely
        post["html_content"] = render_markdown(post.get("markdown_content") or "")

        # Track view (fire-and-forget; errors don't break the page load)
        async def _track_view():
            try:
                async with get_db() as conn:
                    row = await conn.fetchrow(
                        "SELECT id, views FROM analytics_log WHERE blog_id = $1",
                        post["id"]
                    )
                    if row:
                        await conn.execute(
                            "UPDATE analytics_log SET views = $1 WHERE blog_id = $2",
                            int(row["views"]) + 1, post["id"],
                        )
                    else:
                        await conn.execute(
                            "INSERT INTO analytics_log (blog_id, views) VALUES ($1, 1)",
                            post["id"],
                        )
            except Exception as view_err:
                logger.warning("View tracking failed for slug=%s: %s", slug, view_err)

        asyncio.create_task(_track_view())

        return templates.TemplateResponse(
            "post.html",
            {
                "request":   request,
                "post":      post,
                "site_name": settings.app_name,
                "base_url":  settings.base_url,
            },
        )
    except Exception as exc:
        logger.exception("Blog post error for slug=%s", slug)
        return HTMLResponse(
            content=_error_page(500, "Post Error", str(exc)), status_code=500
        )


# ---------------------------------------------------------------------------
# Internal API Routes
# ---------------------------------------------------------------------------
@app.post("/api/generate", response_model=APIResponse, tags=["Internal API"])
async def api_generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    """Trigger the System 1 LangGraph content-generation workflow."""
    try:
        from graph_system1 import run_generation_graph
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"Generation system unavailable: {e}")
    background_tasks.add_task(run_generation_graph, req.topic, req.niche, chat_id=None)
    return APIResponse(
        success=True,
        message=f"Generation started for topic: '{req.topic}' in niche '{req.niche}'",
    )


@app.post("/api/optimize", response_model=APIResponse, tags=["Internal API"])
async def api_optimize(req: OptimizeRequest, background_tasks: BackgroundTasks):
    """Trigger the System 2 LangGraph SEO-optimization workflow."""
    try:
        from graph_system2 import run_optimization_graph
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"Optimization system unavailable: {e}")
    background_tasks.add_task(
        run_optimization_graph,
        threshold_views=req.threshold_views,
        threshold_seo=req.threshold_seo,
        chat_id=None,
    )
    return APIResponse(success=True, message="Optimization pipeline started")


@app.get("/api/stats", tags=["Internal API"])
async def api_stats():
    """Return top 5 blogs by views."""
    try:
        return await fetch_top_blogs(5)
    except Exception as exc:
        logger.exception("API stats error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/health", tags=["Internal API"])
async def health():
    """Simple liveness probe."""
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/trending", tags=["Internal API"])
async def api_trending():
    """Fetch trending topics from HackerNews, Reddit, and GitHub Trending."""
    try:
        from clients.trends_client import TrendsClient
        client = TrendsClient()
        topics = await client.fetch_trending(limit=20)
        return {"topics": topics, "count": len(topics)}
    except Exception as exc:
        logger.exception("Trending topics error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/scheduler/status", tags=["Internal API"])
async def api_scheduler_status():
    """Return daily scheduler status and next run time."""
    try:
        from scheduler import scheduler
        return scheduler.status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/scheduler/run", response_model=APIResponse, tags=["Internal API"])
async def api_scheduler_run(background_tasks: BackgroundTasks):
    """Manually trigger a daily batch run immediately."""
    try:
        from scheduler import scheduler
        background_tasks.add_task(scheduler.daily_batch, True)
        return APIResponse(success=True, message="Daily batch triggered manually")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _error_page(code: int, title: str, detail: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>{code} — {title}</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap" rel="stylesheet"/>
  <style>
    body {{font-family:Inter,sans-serif;background:#0a0b0f;color:#e8eaf0;
          display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}}
    .box {{text-align:center;max-width:500px;padding:40px;}}
    .code {{font-size:6rem;font-weight:800;
            background:linear-gradient(135deg,#6c63ff,#a78bfa);
            -webkit-background-clip:text;-webkit-text-fill-color:transparent;}}
    h1   {{font-size:1.6rem;margin:12px 0;}}
    p    {{color:#6b7280;font-size:.9rem;}}
    a    {{color:#a78bfa;text-decoration:none;}}
  </style>
</head>
<body>
  <div class="box">
    <div class="code">{code}</div>
    <h1>{title}</h1>
    <p>{detail}</p>
    <p style="margin-top:24px"><a href="/">← Back to Home</a></p>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=settings.app_port,
        reload=False,
        log_level="info",
    )
