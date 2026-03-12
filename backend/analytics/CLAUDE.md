# Analytics — WhatsApp Data Extraction

## Purpose
Scripts to fetch and parse WhatsApp conversations from GreenAPI for tenant maintenance request analysis.

## Tech Stack
- **API**: GreenAPI WhatsApp Business API
- **Language**: Python (requests, dotenv)

## Files
```
analytics/
├── get_chats.py              # Fetch WhatsApp chats from GreenAPI
├── extract_conversations.py  # Parse raw chat data into conversation files
├── chats.json                # Cached WhatsApp chat list
└── conversations/            # Extracted conversation text files
```

## Running
```bash
source ../.venv/bin/activate
python analytics/get_chats.py              # fetch chats
python analytics/extract_conversations.py  # extract conversations
```

## Configuration
Requires `.env` in project root with:
- `ID_INSTANCE` — GreenAPI instance ID
- `API_TOKEN_INSTANCE` — GreenAPI API token
