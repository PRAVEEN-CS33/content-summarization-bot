import asyncio
from processing.on_demand import process_on_demand_async
from transcriber.whisper_transcriber import _full_pipeline_sync

async def main():
    print("Testing transcription directly on URL:", "https://www.youtube.com/watch?v=x5Ue0-BDX94")
    content = _full_pipeline_sync("https://www.youtube.com/watch?v=x5Ue0-BDX94")
    print("Content extracted:", len(content) if content else "None")

if __name__ == "__main__":
    asyncio.run(main())
