from discovery.spotify import _scrape_spotify_show_name, resolve_spotify
import feedparser
import requests
import re
import config

def extract_spotify_audio(spotify_url: str) -> str:
    # 1. Scrape episode title
    bot_headers = {"User-Agent": "SpotifyBot/1.0"}
    resp = requests.get(spotify_url, timeout=config.REQUEST_TIMEOUT, headers=bot_headers)
    episode_title = None
    
    m = re.search(r"<title>([^<]+)</title>", resp.text, re.IGNORECASE)
    if m:
        title_text = m.group(1).replace("&amp;", "&").strip()
        if "Web Player" not in title_text:
            parts = title_text.split("|")
            if len(parts) > 1:
                left_side = parts[0].strip()
                # Find the last dash
                idx = left_side.rfind("-")
                if idx > 0:
                    episode_title = left_side[:idx].strip()
                    
    # fallback
    if not episode_title:
        m2 = re.search(r'<meta property="og:title"\s+content="([^"]+)"', resp.text, re.IGNORECASE)
        if m2:
            episode_title = m2.group(1).replace("&amp;", "&").strip()
    
    # 2. Resolve RSS
    res = resolve_spotify(spotify_url)
    if not res or not episode_title: 
        return spotify_url
    _, show_name, rss_url = res
    print(f"Scraped Episode: {episode_title}")
    
    # 3. Download feed and find mp3
    try:
        feed = feedparser.parse(rss_url)
        for entry in feed.entries[:30]:
            print(f"Checking {entry.title}")
            # simple normalized matching
            if normalize(episode_title) in normalize(entry.title) or normalize(entry.title) in normalize(episode_title):
                # match!
                for enc in getattr(entry, 'enclosures', []):
                    if enc.get('type','').startswith('audio/'):
                        return enc.get('href')
    except Exception as e:
        print(e)
    return spotify_url

def normalize(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())

print(extract_spotify_audio("https://open.spotify.com/episode/2lErM1P0U3NMHlQHOrnoKM"))
