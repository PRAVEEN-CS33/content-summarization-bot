# 🤖 Personal AI Content Summarizer

Auto-fetch → Transcribe → Summarize → Telegram delivery.
Fully local. No cloud LLMs. No subscriptions.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        YOUR LAPTOP (16GB RAM)                   │
│                                                                 │
│  ┌─────────────┐    ┌──────────────────────────────────────┐   │
│  │  TELEGRAM   │    │            SCHEDULER                  │   │
│  │    BOT      │◄───│  ┌─────────────┐ ┌────────────────┐  │   │
│  │  (commands) │    │  │ fetch_job   │ │  process_job   │  │   │
│  └──────┬──────┘    │  │ (60 min)    │ │  (60 min + 5)  │  │   │
│         │           │  └──────┬──────┘ └───────┬────────┘  │   │
│         │           │         │                │            │   │
│         │           └─────────┼────────────────┼────────────┘   │
│         │                     │                │                │
│         ▼                     ▼                ▼                │
│  ┌─────────────┐    ┌──────────────┐   ┌──────────────────┐    │
│  │  DISCOVERY  │    │ RSS MONITOR  │   │   PROCESSING     │    │
│  │   ENGINE    │    │              │   │    PIPELINE      │    │
│  │ • YouTube   │    │ feedparser   │   │                  │    │
│  │ • Podcast   │    │ dedup check  │   │ ┌─────────────┐  │    │
│  │ • G.Alerts  │    │ queue new    │   │ │  EXTRACTOR  │  │    │
│  └──────┬──────┘    └──────┬───────┘   │ │ description │  │    │
│         │                  │           │ │ transcript  │  │    │
│         │                  │           │ │ article     │  │    │
│         ▼                  ▼           │ └──────┬──────┘  │    │
│  ┌─────────────────────────────────┐  │        │          │    │
│  │         SQLITE DATABASE         │  │ ┌──────▼──────┐  │    │
│  │  sources | items | summaries    │  │ │  WHISPER    │  │    │
│  └─────────────────────────────────┘  │ │ (podcasts)  │  │    │
│                                       │ └──────┬──────┘  │    │
│                                       │        │          │    │
│                                       │ ┌──────▼──────┐  │    │
│                                       │ │   OLLAMA    │  │    │
│                                       │ │  mistral:7b │  │    │
│                                       │ └─────────────┘  │    │
│                                       └──────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
content_summarizer/
├── main.py                        ← Entry point
├── config.py                      ← All settings (reads .env)
├── requirements.txt
├── .env.example                   ← Copy to .env and fill in
│
├── bot/
│   ├── handlers.py                ← All Telegram commands
│   └── formatter.py               ← Message formatting (MarkdownV2)
│
├── discovery/
│   ├── youtube.py                 ← Channel name → RSS URL
│   ├── podcast.py                 ← Podcast name → RSS URL
│   └── google_alerts.py          ← Topic → Google News RSS
│
├── rss_manager/
│   └── feed_monitor.py            ← Fetch feeds, detect new entries
│
├── processing/
│   └── pipeline.py                ← Orchestrate: extract → transcribe → summarize
│
├── transcriber/
│   └── whisper_transcriber.py     ← yt-dlp + faster-whisper
│
├── summarizer/
│   └── ollama_summarizer.py       ← Ollama API + prompt template
│
├── scheduler/
│   └── cron_jobs.py               ← APScheduler jobs
│
├── database/
│   ├── models.py                  ← SQL schema
│   └── db.py                      ← All CRUD operations
│
└── utils/
    ├── logger.py                  ← Structured logging
    └── retry.py                   ← Retry decorator
```

---

## Step-by-Step Setup

### Step 1 — Install System Dependencies

```bash
# Python 3.11+
python3 --version

# ffmpeg (required by Whisper for audio processing)
sudo apt install ffmpeg                  # Ubuntu/Debian
brew install ffmpeg                      # macOS

# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh
```

### Step 2 — Pull LLM Model

```bash
# Best for 16GB RAM — good quality, fast
ollama pull mistral:7b-instruct

# OR lighter option (faster, lower quality)
ollama pull llama3.2:3b

# Start the Ollama server
ollama serve
```

### Step 3 — Create Your Telegram Bot

```
1. Open Telegram → search @BotFather
2. Send: /newbot
3. Choose a name and username
4. Copy the API token → paste into .env as TELEGRAM_BOT_TOKEN

5. Find your Chat ID:
   - Search @userinfobot on Telegram
   - Start it → it replies with your chat ID
   - Paste into .env as TELEGRAM_CHAT_ID
```

### Step 4 — Get Free API Keys (Optional but recommended)

**Podcast Index API** (free, for better podcast search):
```
1. Go to https://podcastindex.org/
2. Register → get API Key + Secret
3. Paste into .env
```

**YouTube Data API** (optional — scraping works without it):
```
1. Go to https://console.cloud.google.com/
2. Enable YouTube Data API v3
3. Create credentials → API Key
4. Paste into .env
```

### Step 5 — Install Python Dependencies

```bash
git clone <repo>
cd content_summarizer

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### Step 6 — Configure Environment

```bash
cp .env.example .env
nano .env                         # Fill in your tokens
```

### Step 7 — Run

```bash
python main.py
```

---

## Telegram Commands Reference

| Command | Description | Example |
|---|---|---|
| `/start` | Show help | `/start` |
| `/add youtube` | Add YouTube channel | `/add youtube @mkbhd` |
| `/add podcast` | Add podcast | `/add podcast Lex Fridman` |
| `/add topic` | Add news topic | `/add topic AI startups India` |
| `/list` | Show all sources | `/list` |
| `/remove <id>` | Remove a source | `/remove 3` |
| `/summarize` | Manual fetch + process | `/summarize` |
| `/summary today` | Show today's summaries | `/summary today` |
| `/status` | System health check | `/status` |

---

## Performance Tips for 16GB RAM

| Concern | Recommendation |
|---|---|
| LLM RAM usage | `mistral:7b` uses ~5-6GB. Use `llama3.2:3b` (2GB) if tight |
| Whisper RAM | `base` model = ~1GB. Avoid `large` on CPU |
| Concurrent jobs | `max_instances=1` on all jobs prevents memory spikes |
| Podcast audio | Limit to 2-hour episodes (`AUDIO_MAX_DURATION_SEC=7200`) |
| DB performance | WAL mode enabled — handles concurrent reads fine |
| Startup time | Whisper loads lazily on first transcription request |

---

## Recommended Model Combinations

| Use Case | Ollama Model | Whisper Model | Est. RAM |
|---|---|---|---|
| **Best quality** | `mistral:7b-instruct` | `small` | ~8GB |
| **Balanced** ✅ | `mistral:7b-instruct` | `base` | ~6GB |
| **Lightweight** | `llama3.2:3b` | `base` | ~4GB |
| **Minimal** | `phi3:mini` | `tiny` | ~3GB |

---

## Database Schema

```sql
sources          — YouTube channels, podcasts, topics you follow
processed_items  — Every RSS entry seen (dedup key: source_id + entry_id)
summaries        — Final LLM summaries, with sent_telegram flag
```

---

## Error Recovery

- All jobs have `max_instances=1` — no duplicate runs
- `retry()` decorator with exponential backoff on all network calls
- Failed items stay in DB with `status='failed'` + error message
- Logs written to `data/logs/app.log`
- Whisper and Ollama load lazily — bot starts even if they're offline

---

## Running as a Service (Always On)

```bash
# Create systemd service
sudo nano /etc/systemd/system/content-summarizer.service
```

```ini
[Unit]
Description=AI Content Summarizer
After=network.target

[Service]
User=your_username
WorkingDirectory=/home/your_username/content_summarizer
ExecStart=/home/your_username/content_summarizer/venv/bin/python main.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable content-summarizer
sudo systemctl start content-summarizer
sudo journalctl -u content-summarizer -f  # live logs
```
