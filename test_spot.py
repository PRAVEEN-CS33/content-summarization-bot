import asyncio
from transcriber.whisper_transcriber import transcribe_url_async
async def main():
    u = await transcribe_url_async("https://open.spotify.com/episode/2lErM1P0U3NMHlQHOrnoKM")
    print(u)

asyncio.run(main())
