---
description: Spotify content summarization technical workflow
---

# Spotify Summarization Workflow (Technical Reference)

This workflow describes the end-to-end technical process for summarizing Spotify podcasts and episodes within the NaradaAI system.

## 1. Input & Detection

- **Trigger**: User provides a Spotify URL (e.g., `open.spotify.com/episode/...` or `/add spotify <name>`).
- **Detection**: `detect_url_type()` in `processing/on_demand.py` identifies the link as "audio".

## 2. Resource Resolution (`discovery/spotify.py`)

Because Spotify content is DRM-protected, the system must resolve it to a public, DRM-free RSS feed.

1. **Information Scraping**:
   - The bot scrapes the Spotify URL using `BOT_HEADERS` (`SpotifyBot/1.0`).
   - It extracts the **Show Name**, **Episode Title**, and **Description** from meta tags and the `<title>`.
   - For episodes, it also scrapes the page HTML for the parent `show_id` (using regex for `spotify.com/show/ID` or `spotify:show:ID`).

2. **RSS Discovery (Multi-Strategy)**:
   The system attempts to find the corresponding RSS feed in this priority order:
   - **Podcast Index API**: Search by show name (requires API keys in `.env`).
   - **iTunes Search API**: Search by show name (publicly accessible).
   - **gPodder Search**: Search by show name.
   - **RSS Bridge**: Direct proxying of the `show_id` through templates like `feeds.pod.co/{show_id}` or `spotifyrss.com`.

## 3. Extraction Pipeline (`processing/on_demand.py`)

1. **Phase A: Gemini URL-First**:
   - The system calls `gemini_summarizer.summarize_from_url()`.
   - **Technical Change**: For Spotify/generic podcasts, the Gemini SDK is called with a **pure text prompt** (no `Part.from_uri`). This allows Gemini to use its internal browsing/knowledge to summarize if the content is known, avoiding "400 Invalid Argument" errors.

2. **Phase B: Local Transcription Fallback**:
   If Gemini cannot access the URL directly, the system falls back to the audio processing pipeline:
   - **MP3 Extraction**: `extract_spotify_audio_url()` takes the resolved RSS feed and matches the requested episode title to find the direct `<enclosure>` MP3 link.
   - **Local Processing**: If an MP3 is found, it is downloaded and transcribed using the **Whisper** engine.
   - **DRM Guard**: If no RSS/MP3 can be resolved, the pipeline stops with a clean error, preventing `yt-dlp` from crashing on DRM content.

## 4. Summarization & Formatting

- The content (either from Gemini's browsing or the Whisper transcript) is passed to `gemini_summarizer.summarize()`.
- The summary is formatted into a Telegram-friendly HTML message with an "OVERVIEW" and "SUMMARY" (bullet points) section.
- The **Source Link** is appended at the bottom.

## 5. Scheduling (Future Updates)

- Once a Spotify source is resolved and added, `feed_monitor.py` tracks the resolved RSS URL just like any other podcast, ensuring future episodes are summarized automatically during scheduled runs.
