# 🚀 Blog Empire — Closed-Loop Autonomous Blogging Platform

> **Self-healing SEO content engine** — AI researches, writes, fact-checks, publishes, and optimizes blog posts **fully autonomously**. Control everything via Telegram chat or REST API.

[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green)](https://fastapi.tiangolo.com)
[![LLM](https://img.shields.io/badge/LLM-Qwen3--32B%20via%20Groq-purple)](https://groq.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ✨ What It Does

| Feature | Details |
|---------|---------|
| **AI Content Generation** | Researches topic → writes 1500-2500 word markdown post → fact-checks → publishes |
| **Self-Healing SEO** | Automatically identifies and rewrites low-performing posts to improve traffic |
| **Conversational AI Agent** | Chat naturally with the Telegram bot — no commands needed. Just say *"write a blog about Rust"* |
| **Multi-platform Syndication** | Auto-publishes to Dev.to and Hashnode with canonical URL for SEO backlinks |
| **Daily Auto-Scheduler** | Every morning at 09:00 auto-fetches trending topics (HN + Reddit + GitHub) and publishes 5 posts |
| **Duplicate Detection** | Checks if similar content exists before generating; warns you with a link |
| **Trending Topics** | Free, no-API-key sources: HackerNews, Reddit (r/programming, r/ML), GitHub Trending |
| **Real-time View Counting** | Every blog page visit increments the view counter instantly |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                    You (Telegram)                    │
│  "write a blog about Rust performance"               │
└────────────────────┬────────────────────────────────┘
                     │ natural language
                     ▼
┌─────────────────────────────────────────────────────┐
│            AI Agent  (agent.py)                      │
│  Qwen3-32B understands intent → dispatches action   │
└───────┬──────────────┬──────────────┬───────────────┘
        │              │              │
        ▼              ▼              ▼
  ┌──────────┐  ┌──────────┐  ┌──────────────┐
  │ System 1 │  │ System 2 │  │  Scheduler   │
  │ LangGraph│  │ LangGraph│  │  (daily 9am) │
  │ Content  │  │ SEO Opt. │  │  5 auto posts│
  └────┬─────┘  └────┬─────┘  └──────┬───────┘
       │              │               │
       ▼              ▼               │
┌─────────────────────────────────────┐
│         SQLite Database             │
│  Published_Blogs  Analytics_Log     │
│  Content_Tree                       │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│      FastAPI Website + API          │
│  /  Blog Homepage  /blog/{slug}     │
│  /api/generate  /api/optimize       │
│  /api/stats  /api/trending          │
└──────────────┬──────────────────────┘
               │
        ┌──────┴──────┐
        ▼             ▼
   ┌─────────┐  ┌──────────┐
   │ Dev.to  │  │ Hashnode │
   │ Syndic. │  │ Syndic.  │
   └─────────┘  └──────────┘
```

---

## 📁 Project Structure

```
blog_empire/
├── main.py               # FastAPI app + web routes + API endpoints
├── bot.py                # Telegram bot (aiogram 3.x) + all commands
├── agent.py              # 🆕 Conversational AI agent (intent router)
├── scheduler.py          # 🆕 Daily auto-blog scheduler (09:00, 5 posts)
├── database.py           # SQLite schema + async CRUD helpers
├── graph_system1.py      # LangGraph: Content generation pipeline
│                         #   Scout → Writer → Revisor → Publisher
├── graph_system2.py      # LangGraph: SEO optimization pipeline
│                         #   Ingestion → Diagnostic → Optimizer → Update
├── models.py             # Pydantic models + LangGraph TypedDict states
├── config.py             # pydantic-settings config from .env
│
├── clients/
│   ├── groq_client.py    # Groq API (Qwen3-32B) — all LLM calls
│   ├── devto_client.py   # Dev.to REST API syndication
│   ├── hashnode_client.py# Hashnode GraphQL syndication
│   └── trends_client.py  # 🆕 Free trending topics (HN + Reddit + GitHub)
│
├── templates/            # Jinja2 HTML templates
│   ├── index.html        # Homepage (blog list)
│   └── post.html         # Single post page
│
├── static/               # CSS + JS assets
│   ├── css/
│   └── js/
│
├── test.py               # 12-test integration test suite
├── render.yaml           # Render.com deployment config
├── requirements.txt      # Python dependencies
└── .env.example          # Environment variable template
```

---

## ⚙️ Setup (Local)

### Prerequisites
- Python 3.11+
- A [Groq API key](https://console.groq.com) (free)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### 1. Clone & Install

```bash
git clone https://github.com/yourname/blog-empire.git
cd blog-empire
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your real values:
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | ✅ | From [console.groq.com](https://console.groq.com) |
| `GROQ_MODEL` | ✅ | `qwen/qwen3-32b` (don't change) |
| `TELEGRAM_BOT_TOKEN` | ✅ | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_ADMIN_CHAT_ID` | ✅ | Your Telegram user ID — get from [@userinfobot](https://t.me/userinfobot) |
| `BASE_URL` | ✅ | `http://localhost:8000` locally; your public URL when deployed |
| `DEVTO_API_KEY` | Optional | From [dev.to/settings/extensions](https://dev.to/settings/extensions) |
| `HASHNODE_API_TOKEN` | Optional | From [hashnode.com/settings/developer](https://hashnode.com/settings/developer) |
| `HASHNODE_PUBLICATION_ID` | Optional | Your Hashnode blog ID |
| `APP_PORT` | Optional | Default: `8000` |
| `SEO_THRESHOLD_VIEWS` | Optional | Default: `100` |
| `SEO_THRESHOLD_SCORE` | Optional | Default: `50.0` |
| `MAX_REVISIONS` | Optional | Default: `3` |

### 3. Run

```bash
python main.py
```

- **Website** → `http://localhost:8000`
- **API Docs** → `http://localhost:8000/docs`
- **Telegram bot** → Open your bot and start chatting

---

## 🤖 Using the AI Agent

The bot has a **conversational AI agent** powered by Qwen3-32B. Just chat naturally — no slash commands needed:

| What you type | What happens |
|--------------|-------------|
| `"write a blog about Rust async runtime"` | Generates a full blog post |
| `"write about Python anyway, I don't care about duplicates"` | Force-generates (bypasses duplicate check) |
| `"optimize my posts"` | Runs SEO optimizer on all low-performing content |
| `"what's trending today?"` | Fetches live trends from HN + Reddit + GitHub |
| `"show me my stats"` | Displays top 5 posts by view count |
| `"how is the scheduler doing?"` | Shows next batch time and last count |
| `"hey what can you do?"` | Shows full capability overview |
| Anything else | Natural AI conversation |

### Slash Commands (also work)

```
/start             Show all commands
/generate          Write blog with duplicate check
/generate_force    Write blog bypassing duplicate check
/optimize          Run SEO optimizer
/stats             View top posts
/schedule          Scheduler status
/trending          Live trending topics
```

---

## 🌐 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Blog homepage |
| `GET` | `/blog/{slug}` | Individual blog post |
| `POST` | `/api/generate` | Trigger content generation |
| `POST` | `/api/optimize` | Trigger SEO optimization |
| `GET` | `/api/stats` | Top 5 blogs by views |
| `GET` | `/api/health` | Liveness probe |
| `GET` | `/api/trending` | Fetch trending topics |
| `GET` | `/api/scheduler/status` | Scheduler status |
| `POST` | `/api/scheduler/run` | Manually trigger daily batch |

Full interactive docs at `http://localhost:8000/docs`

---

## 🚀 Deploy to Render

> **Yes — Render works perfectly.** One-click deploy from GitHub.

### Step-by-Step

**1. Push your code to GitHub**
```bash
git init && git add . && git commit -m "Blog Empire v1"
git remote add origin https://github.com/yourname/blog-empire.git
git push -u origin main
```

**2. Create a Render Web Service**
1. Go to [render.com](https://render.com) → **New → Web Service**
2. Connect your GitHub repo
3. Render auto-detects `render.yaml` — click **Deploy**

**3. Set Environment Variables**
In Render Dashboard → your service → **Environment**:
- `GROQ_API_KEY` = your key
- `TELEGRAM_BOT_TOKEN` = your token
- `TELEGRAM_ADMIN_CHAT_ID` = your Telegram user ID
- `BASE_URL` = `https://your-app-name.onrender.com` ← **important for syndication**
- Optional: `DEVTO_API_KEY`, `HASHNODE_API_TOKEN`, `HASHNODE_PUBLICATION_ID`

**4. Persistent Database (Recommended)**

> ⚠️ Render free tier has **no persistent disk** — SQLite resets on every redeploy.
> For production use, upgrade to **Starter plan ($7/mo)** and add a disk:

In Render → your service → **Disks** → **Add Disk**:
- Mount Path: `/data`
- Size: 1 GB

Then add environment variable: `DB_PATH=/data/blog_empire.db`

### Render Free Tier Limitations

| Limitation | Impact |
|-----------|--------|
| Service sleeps after 15 min inactivity | Bot wakes the service on first message |
| No persistent disk | DB resets on redeploy — use Starter plan |
| 512 MB RAM | Sufficient for this stack |
| 0.1 CPU | Groq API calls are network-bound, fine |

### Alternative: Always-On on Render Free

Add a UptimeRobot (free) monitor pointing to your `/api/health` endpoint — it pings every 5 minutes keeping the service awake.

---

## 🧪 Running Tests

```bash
python test.py
# Expected: 12/12 tests passed — ALL GOOD!
```

Tests cover: DB CRUD, concurrent connections, Pydantic models, bot HTML formatting, LangGraph TypedDict state merging, full System 1 pipeline (mocked), FastAPI routes.

---

## 🔄 How the Content Pipeline Works

```
1. SCOUT    → Qwen3-32B researches topic (context, keywords, trends)
2. WRITER   → Qwen3-32B writes 1500-2500 word markdown post
3. REVISOR  → Qwen3-32B fact-checks against researched context
   ↑              if hallucination detected:
   └── loops back to WRITER with revision notes (max 3 revisions)
4. PUBLISHER → Saves to SQLite, syndicates to Dev.to + Hashnode, sends Telegram alert
```

---

## 📊 How View Counting Works

Every visit to `/blog/{slug}` fires an async background task that increments `Analytics_Log.views` in the DB. The homepage and `/api/stats` read from this table. No external analytics service needed.

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | FastAPI + Jinja2 templates |
| LLM | Groq API — Qwen3-32B (32B param, fastest inference) |
| Orchestration | LangGraph (stateful AI pipelines) |
| Database | SQLite via aiosqlite (async) |
| Telegram | aiogram 3.x |
| Scheduler | Pure asyncio (no extra deps) |
| Syndication | Dev.to REST API, Hashnode GraphQL |
| Trending | HackerNews Firebase, Reddit JSON, GitHub HTML |
| Config | pydantic-settings |

---

## 📄 License

MIT — free to use, modify, and deploy.
