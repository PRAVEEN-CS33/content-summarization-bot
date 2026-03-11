# import feedparser
# feed = feedparser.parse("https://www.google.co.in/alerts/feeds/15473924157373475664/2040260247688232025")

# for entry in feed.entries:
#     print(entry.title)
#     print(entry.link)
#     print("---")


from rss_manager.feed_monitor import _fetch_feed

feed = _fetch_feed("https://news.google.com/rss/search?q=https%3A//www.google.co.in/alerts/feeds/15473924157373475664/2040260247688232025%20when%3A1d&hl=en&gl=IN&ceid=IN:en")
print(feed)