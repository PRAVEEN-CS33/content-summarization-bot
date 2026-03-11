import asyncio
from discovery.spotify import resolve_spotify
import feedparser

def test_resolve():
    url = "https://open.spotify.com/episode/2lErM1P0U3NMHlQHOrnoKM"
    result = resolve_spotify(url)
    if result:
        sid, name, rss_url = result
        print(f"RSS URL: {rss_url}")
        
        feed = feedparser.parse(rss_url)
        print(f"Found {len(feed.entries)} entries")
        
        if feed.entries:
            entry = feed.entries[0]
            print(f"Title: {entry.title}")
            
            # Find enclosure
            mp3_url = None
            if hasattr(entry, 'enclosures') and entry.enclosures:
                for enc in entry.enclosures:
                    if enc.get('type", "").startswith("audio/'):
                        mp3_url = enc.get('href')
                        break
            
            print(f"MP3 URL: {mp3_url}")

if __name__ == "__main__":
    test_resolve()
