# 🚀 Blog Empire — Autonomous AI Blogging Platform

> **Self-healing, news-aware AI content engine** — reads today's news, curates viral blog topics, writes 1500-2500 word posts, fact-checks them, and publishes to multiple platforms **fully autonomously**. Control everything via Telegram chat or REST API.

[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green)](https://fastapi.tiangolo.com)
[![LLM](https://img.shields.io/badge/LLM-Qwen3--32B%20via%20Groq-purple)](https://groq.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-orange)](https://github.com/langchain-ai/langgraph)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ✨ What It Does

| Feature | Details |
|---------|---------|
| **📰 News Intelligence Agent** | Reads 3 real-time news APIs daily — curates 5–10 catchy, SEO-ready blog topics using Qwen3-32B |
| **⚡ LangGraph Content Pipeline** | Scout → Writer → Revisor (fact-check loop) → Publisher — fully stateful AI pipeline |
| **🔁 Self-Healing SEO** | Automatically identifies and rewrites low-performing posts to improve traffic |
| **💬 Discussion → Blog Mode** | Have a conversation with your bot about any topic → `/writeblog` publishes it |
| **🤖 Conversational AI Agent** | Chat naturally: *"write a blog about Rust"* — the AI handles the rest |
| **📡 Multi-platform Syndication** | Auto-publishes to Dev.to and Hashnode with canonical URL for SEO backlinks |
| **⏰ Dual Daily Scheduler** | **08:00** — sends topic suggestions to Telegram; **09:00** — auto-generates 5 blogs |
| **♻️ Duplicate Detection** | Vector-similarity check prevents writing the same topic twice |
| **📊 Real-time Analytics** | View counts, platform stats (Hashnode + Dev.to), SEO scores — all in Telegram |

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                    08:00  — News Intelligence Agent                   │
│                                                                       │
│   NewsData.io ─┐                                                      │
│   NewsAPI.org ─┼──► NewsClient ──► LLM Curation ──► 5-10 Topics     │
│   TheNewsAPI  ─┘    (30min cache)   Qwen3-32B        → Telegram      │
│                  ↕ Fallback: HN + Reddit + GitHub (free, no key)     │
└───────────────────────────────────┬──────────────────────────────────┘
                                    │ 09:00 auto-generate
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    ⚡ LangGraph — System 1                            │
│                                                                       │
│  START ──► Scout ──► Writer ──► Revisor ──┐                         │
│                          ▲     (fact-check)│ hallucination?          │
│                          └────────────────┘ max 3 loops              │
│                                    │ clean                            │
│                                    ▼                                  │
│                               Publisher ──► Dev.to / Hashnode / DB   │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
             ┌───────────┐  ┌───────────┐  ┌───────────────┐
             │  Dev.to   │  │ Hashnode  │  │   Website     │
             │  Article  │  │   Post    │  │ /blog/{slug}  │
             └───────────┘  └───────────┘  └───────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                    💬 Discussion → Blog Mode                          │
│                                                                       │
│  /discuss quantum ai in education                                     │
│  [multi-turn chat with Qwen3-32B expert]                              │
│  /writeblog  ──► discussion_context injected into LangGraph pipeline  │
│              ──► published blog that reflects your conversation ✅    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Project Structure

```
blog_empire/
├── main.py               # FastAPI app + web routes + API endpoints
├── bot.py                # Telegram bot (aiogram 3.x) + all commands
├── agent.py              # Conversational AI agent (intent router)
├── scheduler.py          # Dual scheduler: 08:00 recommend, 09:00 generate
├── database.py           # PostgreSQL schema + async CRUD helpers
├── graph_system1.py      # LangGraph System 1: Scout→Writer→Revisor→Publisher
├── graph_system2.py      # LangGraph System 2: SEO optimization pipeline
├── models.py             # Pydantic models + LangGraph TypedDict states
├── config.py             # pydantic-settings config from .env
│
├── agents/
│   └── news_agent.py     # 🆕 LLM-powered news curator (5-10 topic suggestions)
│
├── clients/
│   ├── news_client.py    # 🆕 Multi-source news fetcher (3 APIs + 30min cache)
│   ├── groq_client.py    # Groq API (Qwen3-32B) — all LLM calls
│   ├── devto_client.py   # Dev.to REST API syndication
│   ├── hashnode_client.py# Hashnode GraphQL syndication
│   └── trends_client.py  # Free trending topics (HN + Reddit + GitHub)
│
├── templates/            # Jinja2 HTML templates
│   ├── index.html        # Homepage (blog list)
│   └── post.html         # Single post page
│
├── static/               # CSS + JS assets
├── test.py               # Integration test suite
├── render.yaml           # Render.com deployment config
├── requirements.txt      # Python dependencies
└── .env.example          # Environment variable template
```

---

## ⚙️ Setup (Local)

### Prerequisites
- Python 3.11+
- A [Groq API key](https://console.groq.com) (free tier available)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### 1. Clone & Install

```bash
git clone https://github.com/Arkaprabha13/Blog-Empire.git
cd Blog-Empire
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your real values
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | ✅ | From [console.groq.com](https://console.groq.com) |
| `GROQ_MODEL` | ✅ | `qwen/qwen3-32b` |
| `TELEGRAM_BOT_TOKEN` | ✅ | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_ADMIN_CHAT_ID` | ✅ | Your Telegram user ID — get from [@userinfobot](https://t.me/userinfobot) |
| `BASE_URL` | ✅ | `http://localhost:8000` locally; your public URL when deployed |
| `DEVTO_API_KEY` | Optional | From [dev.to/settings/extensions](https://dev.to/settings/extensions) |
| `HASHNODE_API_TOKEN` | Optional | From [hashnode.com/settings/developer](https://hashnode.com/settings/developer) |
| `HASHNODE_PUBLICATION_ID` | Optional | Your Hashnode blog ID |
| `NEWSDATA_API_KEY` | Optional | From [newsdata.io](https://newsdata.io) — AI/tech/geopolitics news |
| `NEWSAPI_ORG_KEY` | Optional | From [newsapi.org](https://newsapi.org) — broad category coverage |
| `THE_NEWS_API_KEY` | Optional | From [thenewsapi.com](https://www.thenewsapi.com) — tech + business |
| `SEO_THRESHOLD_VIEWS` | Optional | Default: `100` |
| `SEO_THRESHOLD_SCORE` | Optional | Default: `50.0` |
| `MAX_REVISIONS` | Optional | Default: `3` |

### 3. Run

```bash
python main.py
```

- **Website** → `http://localhost:8000`
- **API Docs** → `http://localhost:8000/docs`
- **Telegram bot** → Open your bot and start chatting!

---

## 🤖 Telegram Commands

### News & Content
| Command | Description |
|---------|-------------|
| `/recommend` | 📡 Get 5-10 AI-curated blog topic ideas from today's news |
| `/generate <topic> [niche]` | Write a new blog post (with duplicate check) |
| `/generate_force <topic> [niche]` | Force-generate (bypass duplicate check) |
| `/trending` | Today's trending topics (free sources) |

### Discussion → Blog
| Command | Description |
|---------|-------------|
| `/discuss <topic>` | Start a multi-turn AI conversation on any topic |
| `/writeblog` | Convert the current discussion into a published blog |
| `/enddiscuss` | End discussion without publishing |

### Management
| Command | Description |
|---------|-------------|
| `/list` | All published blogs with platform links |
| `/view <slug>` | View a post's full details |
| `/stats` | Comprehensive stats from Website + Hashnode + Dev.to |
| `/optimize` | Run SEO self-healing optimizer |
| `/syndicate <slug>` | Push a post to Dev.to & Hashnode |
| `/delete <slug>` | Delete a post (with confirmation) |
| `/schedule` | Dual scheduler status (08:00 + 09:00 jobs) |

### AI Agent (Natural Language)
Just type naturally — no slash commands needed:

| What you type | What happens |
|--------------|-------------|
| `"write a blog about Rust async runtime"` | Generates a full blog post |
| `"discuss quantum computing with me"` | Starts a discussion session |
| `"what's trending today?"` | Fetches live trends |
| `"show me my stats"` | Displays top posts by views |
| `"optimize my posts"` | Runs SEO optimizer |
| Anything else | Natural AI conversation |

---

## 🌐 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Blog homepage |
| `GET` | `/blog/{slug}` | Individual blog post |
| `POST` | `/api/generate` | Trigger content generation |
| `POST` | `/api/optimize` | Trigger SEO optimization |
| `GET` | `/api/stats` | Top blogs by views |
| `GET` | `/api/health` | Liveness probe |
| `GET` | `/api/trending` | Fetch trending topics |
| `GET` | `/api/scheduler/status` | Scheduler status |
| `POST` | `/api/scheduler/run` | Manually trigger daily batch |

Full interactive docs at `http://localhost:8000/docs`

---

## 🔄 LangGraph Content Pipeline (System 1)

```
1. SCOUT      → Qwen3-32B researches topic (context, keywords, 2025 trends)
               + if /discuss used: discussion transcript injected here

2. WRITER     → Qwen3-32B writes 1500-2500 word markdown post
               with H2/H3 structure, code examples, SEO hooks

3. REVISOR    → Fact-checks draft against researched context
               if hallucination detected:
               └── loops back to WRITER with revision notes (max 3 revisions)

4. PUBLISHER  → Saves to PostgreSQL DB
               → Syndicates to Dev.to + Hashnode
               → Sends Telegram alert with links
```

---

## 🔄 News Intelligence Flow (Daily 08:00)

```
NewsData.io ─┐
NewsAPI.org  ─┼──► 40 raw articles ──► Qwen3-32B curator ──► 5-10 suggestions
TheNewsAPI   ─┘                         (selects timely,     ──► Telegram message
                                          catchy topics)       with /generate_force
HN + Reddit                                                     shortcuts
  + GitHub   ──► fallback when paid APIs hit daily limits
```

**Niches monitored:** AI/ML/NLP · Data Centres · Big Tech Companies · Geopolitics · International Affairs · Education & EdTech

---

## 🚀 Deploy

The system runs on any cloud that supports Python + Postgres:

### Railway (Recommended — current setup)
1. Push to GitHub
2. New project → Deploy from GitHub repo
3. Add a Postgres database plugin
4. Set environment variables (see table above)
5. Deploy ✅

### Render
1. Connect GitHub repo → **New Web Service**
2. Render auto-detects `render.yaml`
3. Set environment variables
4. Deploy ✅

> ⚠️ Set `BASE_URL` to your public deployment URL for syndication links to work correctly.

---

## 🧪 Running Tests

```bash
python test.py
# Expected: all tests passed ✅
```

Tests cover: DB CRUD, Pydantic models, bot HTML formatting, LangGraph TypedDict state merging, FastAPI routes.

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Web Framework** | FastAPI + Jinja2 templates |
| **LLM** | Groq API — Qwen3-32B (fastest open-weight inference) |
| **AI Orchestration** | LangGraph (stateful multi-node pipelines) |
| **News Sources** | NewsData.io · NewsAPI.org · The News API |
| **Free Fallback** | HackerNews Firebase · Reddit JSON · GitHub Trending |
| **Database** | PostgreSQL via asyncpg |
| **Telegram** | aiogram 3.x |
| **Scheduler** | Pure asyncio (no extra deps) |
| **Syndication** | Dev.to REST API · Hashnode GraphQL |
| **Config** | pydantic-settings |
| **Deployment** | Railway / Render |

---

## 📄 License

MIT — free to use, modify, and deploy.
