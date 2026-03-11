"""
spotify_summarize_temp.py — Summarize any Spotify episode. No paid API needed.

STRATEGY (tries each in order, no API key required for most):
  1. Podcast Index API  — FREE, ~unlimited for personal use, needs free signup
  2. iTunes Search API  — 100% free, no key, no limit
  3. gpodder.net        — 100% free, no key, open source podcast directory
  4. RSS direct scrape  — parse Spotify page to find RSS hint

USAGE:
  python spotify_summarize_temp.py "https://open.spotify.com/episode/1UmcqhOeKOhnEuXo6Pu8Pu"

ONE-TIME SETUP:
  pip install requests feedparser faster-whisper openai beautifulsoup4

OPTIONAL (unlocks Strategy 1 — most reliable):
  Get free Podcast Index key at https://api.podcastindex.org/
  export PODCAST_INDEX_KEY=...
  export PODCAST_INDEX_SECRET=...
"""

import sys, re, os, hashlib, time, tempfile, subprocess, json
import requests, feedparser
from pathlib import Path
from openai import OpenAI

OPENAI_API_KEY         = os.getenv("OPENAI_API_KEY", "")
PODCAST_INDEX_KEY      = os.getenv("PODCAST_INDEX_KEY", "")
PODCAST_INDEX_SECRET   = os.getenv("PODCAST_INDEX_SECRET", "")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NaradaAI/1.0)"}


# ═══════════════════════════════════════════════════════════════
# STEP 1 — Parse Spotify URL
# ═══════════════════════════════════════════════════════════════

def parse_spotify_url(url):
    ep   = re.search(r"spotify\.com/episode/([\w]+)", url)
    show = re.search(r"spotify\.com/show/([\w]+)", url)
    if ep:   return "episode", ep.group(1)
    if show: return "show",    show.group(1)
    return None, None


# ═══════════════════════════════════════════════════════════════
# STEP 2 — Find RSS feed (4 strategies, no paid API needed)
# ═══════════════════════════════════════════════════════════════

def strategy_podcast_index(spotify_url, episode_title=""):
    """Podcast Index API — free signup, ~unlimited personal use."""
    if not PODCAST_INDEX_KEY:
        return None, None
    try:
        epoch = str(int(time.time()))
        auth  = hashlib.sha1((PODCAST_INDEX_KEY + PODCAST_INDEX_SECRET + epoch).encode()).hexdigest()
        hdrs  = {"X-Auth-Date": epoch, "X-Auth-Key": PODCAST_INDEX_KEY,
                 "Authorization": auth, "User-Agent": "NaradaAI/1.0"}

        # Search by Spotify URL directly
        r = requests.get("https://api.podcastindex.org/api/1.0/podcasts/byurl",
                        params={"url": spotify_url}, headers=hdrs, timeout=10)
        data = r.json()
        rss  = data.get("feed", {}).get("url")
        name = data.get("feed", {}).get("title", "")
        if rss:
            print(f"  ✅ Podcast Index found: {name}")
            return rss, name
    except Exception as e:
        print(f"  Podcast Index: {e}")
    return None, None


def strategy_itunes(podcast_name):
    """iTunes Search API — 100% free, no key, no signup."""
    if not podcast_name:
        return None, None
    try:
        r = requests.get("https://itunes.apple.com/search",
                        params={"term": podcast_name, "media": "podcast", "limit": 5},
                        timeout=10)
        results = r.json().get("results", [])
        for result in results:
            rss  = result.get("feedUrl", "")
            name = result.get("collectionName", "")
            if rss:
                print(f"  ✅ iTunes found: {name}")
                return rss, name
    except Exception as e:
        print(f"  iTunes: {e}")
    return None, None


def strategy_gpodder(podcast_name):
    """gpodder.net — open source podcast directory, free, no key."""
    if not podcast_name:
        return None, None
    try:
        r = requests.get("https://gpodder.net/search.json",
                        params={"q": podcast_name}, timeout=10)
        results = r.json()
        if results:
            rss  = results[0].get("url", "")
            name = results[0].get("title", "")
            if rss:
                print(f"  ✅ gPodder found: {name}")
                return rss, name
    except Exception as e:
        print(f"  gPodder: {e}")
    return None, None


def strategy_scrape_spotify_page(spotify_url):
    """Scrape Spotify episode page to extract podcast name, then search iTunes."""
    try:
        r    = requests.get(spotify_url, headers=HEADERS, timeout=15)
        html = r.text

        # Extract podcast/episode title from og:title or page title
        title_match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html)
        desc_match  = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html)

        title = title_match.group(1) if title_match else ""
        desc  = desc_match.group(1)  if desc_match  else ""

        print(f"  Spotify page title: {title[:60]}")

        # Title is usually "Episode Name | Podcast Name | Spotify"
        # Extract podcast name from it
        parts = [p.strip() for p in title.split("|")]
        podcast_name  = parts[1] if len(parts) >= 2 else parts[0]
        episode_title = parts[0] if len(parts) >= 2 else ""

        return podcast_name.replace(" | Spotify", "").strip(), episode_title, desc

    except Exception as e:
        print(f"  Spotify scrape: {e}")
    return "", "", ""


# ═══════════════════════════════════════════════════════════════
# STEP 3 — Get MP3 from RSS feed
# ═══════════════════════════════════════════════════════════════

def get_mp3_from_rss(rss_url, episode_title=""):
    """Parse RSS and return best matching episode MP3 URL."""
    try:
        feed = feedparser.parse(rss_url)
        if not feed.entries:
            print(f"  ⚠️  RSS has no entries: {rss_url}")
            return None, None

        # Try to match episode title
        if episode_title:
            episode_title_clean = episode_title.lower()
            for entry in feed.entries:
                if episode_title_clean in entry.get("title", "").lower():
                    for enc in getattr(entry, "enclosures", []):
                        if "audio" in enc.get("type","") or enc.get("href","").endswith((".mp3",".m4a")):
                            print(f"  ✅ Matched episode: {entry.get('title','')[:60]}")
                            return enc["href"], entry.get("title","")

        # Fallback: return latest episode
        for entry in feed.entries[:3]:
            for enc in getattr(entry, "enclosures", []):
                href = enc.get("href", "")
                if href and ("audio" in enc.get("type","") or href.endswith((".mp3",".m4a",".ogg"))):
                    print(f"  ✅ Latest episode: {entry.get('title','')[:60]}")
                    return href, entry.get("title","")

    except Exception as e:
        print(f"  RSS parse error: {e}")
    return None, None


# ═══════════════════════════════════════════════════════════════
# STEP 4 — Download MP3
# ═══════════════════════════════════════════════════════════════

def download_mp3(mp3_url, output_path):
    print(f"⬇️  Downloading audio...")
    try:
        r = requests.get(mp3_url, stream=True, timeout=120,
                        headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        with open(output_path, "wb") as f:
            downloaded = 0
            for chunk in r.iter_content(8192):
                f.write(chunk)
                downloaded += len(chunk)
                if downloaded % (1024*1024) == 0:
                    print(f"  {downloaded//(1024*1024)} MB...", end="\r")
        mb = Path(output_path).stat().st_size / (1024*1024)
        print(f"  ✅ Downloaded {mb:.1f} MB")
        return True
    except Exception as e:
        print(f"  Direct download failed: {e}")

    # Fallback: yt-dlp (works for some hosts)
    try:
        result = subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "mp3", "-o", output_path, mp3_url],
            capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  yt-dlp fallback failed: {e}")
    return False


# ═══════════════════════════════════════════════════════════════
# STEP 5 — Transcribe with Whisper
# ═══════════════════════════════════════════════════════════════

def transcribe(audio_path):
    print("🎙  Transcribing with Whisper (base, CPU, 5-min chunks)...")
    try:
        from faster_whisper import WhisperModel
        from pydub import AudioSegment
        import os

        # Initialize model once
        model = WhisperModel("base", device="cpu", compute_type="int8")
        
        # Load audio and determine chunk length
        print("   Loading audio file...")
        audio = AudioSegment.from_file(audio_path)
        chunk_length_ms = 5 * 60 * 1000 # 5 minutes
        chunks = [audio[i:i + chunk_length_ms] for i in range(0, len(audio), chunk_length_ms)]
        
        print(f"   Audio split into {len(chunks)} chunks.")
        
        full_text = ""
        total_duration = 0
        
        for i, chunk in enumerate(chunks, 1):
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_chunk:
                chunk_path = temp_chunk.name
            
            # Export chunk
            try:
                chunk.export(chunk_path, format="mp3")
                print(f"   Transcribing chunk {i}/{len(chunks)}...")
                
                segments, info = model.transcribe(chunk_path, beam_size=1, vad_filter=True)
                chunk_text = " ".join(seg.text.strip() for seg in segments)
                
                full_text += chunk_text + " "
                total_duration += info.duration
                    
            finally:
                if os.path.exists(chunk_path):
                    os.remove(chunk_path)
                    
        print(f"  ✅ {len(full_text.strip())} chars, {total_duration/60:.1f} min total audio transcribed")
        return full_text.strip()
        
    except ImportError as e:
        print(f"  ❌ Missing dependency: {e}")
        print("  Run: pip install faster-whisper pydub")
        return ""
    except Exception as e:
        import traceback
        print(f"  Whisper error: {e}")
        traceback.print_exc()
        return ""


# ═══════════════════════════════════════════════════════════════
# STEP 6 — Summarize with ChatGPT
# ═══════════════════════════════════════════════════════════════

def summarize(title, transcript, description=""):
    print("🤖 Summarizing with ChatGPT (gpt-4o-mini)...")
    content = transcript if len(transcript) > 200 else (description or transcript)
    if not content:
        return "❌ No content to summarize."

    client   = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a concise podcast summarizer."},
            {"role": "user",   "content": f"""Summarize this podcast episode.

TITLE: {title}

CONTENT:
{content}

Format your response as:
📋 OVERVIEW
[2-3 sentences about what this episode is about]

🔑 KEY POINTS
• [point 1]
• [point 2]
• [point 3]

💡 KEY TAKEAWAY
[one actionable insight]

⏱️ WORTH LISTENING IF:
[one sentence about who should listen]"""},
        ],
        temperature=0.3,
        max_tokens=500,
    )
    return response.choices[0].message.content.strip()


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else \
          "https://open.spotify.com/episode/1UmcqhOeKOhnEuXo6Pu8Pu"

    print(f"\n🎵 Spotify Episode Summarizer")
    print(f"   {url}\n")

    _, episode_id = parse_spotify_url(url)
    if not episode_id:
        print("❌ Not a valid Spotify episode URL")
        sys.exit(1)

    # ── Find RSS feed ──────────────────────────────────────────
    rss_url = None
    episode_title = ""
    page_description = ""

    # First scrape Spotify page to get podcast name (needed for iTunes/gPodder)
    print("📡 Finding podcast RSS feed...")
    podcast_name, episode_title, page_description = strategy_scrape_spotify_page(url)
    print(f"   Podcast: '{podcast_name}', Episode: '{episode_title}'")

    # Try strategies in order
    for name, fn in [
        ("Podcast Index", lambda: strategy_podcast_index(url, episode_title)),
        ("iTunes",        lambda: strategy_itunes(podcast_name)),
        ("gPodder",       lambda: strategy_gpodder(podcast_name)),
    ]:
        print(f"\n🔍 Trying {name}...")
        rss_url, found_name = fn()
        if rss_url:
            break

    if not rss_url:
        print("\n❌ Could not find RSS feed automatically.")
        print("💡 Manual fix: Go to https://podcastindex.org, search the show,")
        print("   copy the RSS URL and run:")
        print("   python spotify_summarize_temp.py <RSS_URL>")
        if page_description:
            print("\n📝 Summarizing from Spotify page description only...")
            summary = summarize(episode_title or "Spotify Episode", "", page_description)
            print(f"\n{'='*60}\n{summary}\n{'='*60}")
        sys.exit(1)

    # ── Get MP3 URL from RSS ───────────────────────────────────
    print(f"\n🎧 Finding episode MP3 in RSS feed...")
    mp3_url, ep_title = get_mp3_from_rss(rss_url, episode_title)
    final_title = ep_title or episode_title or "Spotify Episode"

    if not mp3_url:
        print("❌ No MP3 found in RSS feed.")
        if page_description:
            print("📝 Summarizing from description only...")
            summary = summarize(final_title, "", page_description)
            print(f"\n{'='*60}\n{summary}\n{'='*60}")
        sys.exit(1)

    # ── Download + Transcribe + Summarize ─────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = str(Path(tmp) / "episode.mp3")
        ok = download_mp3(mp3_url, audio_path)

        if not ok:
            print("⚠️  Download failed — summarizing from description only")
            summary = summarize(final_title, "", page_description)
        else:
            transcript = transcribe(audio_path)
            summary    = summarize(final_title, transcript, page_description)

    # ── Print result ───────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"📌 {final_title}")
    print(f"{'='*60}")
    print(summary)
    print(f"{'='*60}\n")

    out = "spotify_summary.txt"
    with open(out, "w") as f:
        f.write(f"URL: {url}\nTitle: {final_title}\n\n{summary}")
    print(f"💾 Saved to {out}")


if __name__ == "__main__":
    main()