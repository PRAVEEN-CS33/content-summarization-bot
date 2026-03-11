# YouTube Summarization Workflow

```mermaid
flowchart TD
    A[New YouTube URL/RSS Item] --> B{Is it a Short?}
    B -- Yes --> C[Skip Processing]
    B -- No --> D[Attempt Download Subtitles]
    D --> E{Subtitles Available?}
    E -- Yes --> F[Extract Text from VTT]
    E -- No --> G[Download Audio (yt-dlp)]
    G --> H[Transcribe with Whisper]
    F --> I[Truncate to 12,000 chars]
    H --> I
    I --> J[Send to OpenAI API (gpt-4o-mini)]
    J --> K[Generate Summary & Key Insights]
    K --> L[Format with HTML & Add Emojis]
    L --> M[Push to Telegram]
```
