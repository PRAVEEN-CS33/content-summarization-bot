import requests
import re

url = "https://open.spotify.com/episode/2lErM1P0U3NMHlQHOrnoKM"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/112.0",
    "Accept-Language": "en-US,en;q=0.5",
}
resp = requests.get(url, headers=headers)

m = re.search(r"<title>([^<]+?)</title>", resp.text)
if m:
    print(m.group(1))
