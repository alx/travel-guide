# Install on lamai270

Replace `alx` with your username if different.

## 1. Copy systemd unit files

```bash
sudo cp gps-llama-server.service  /etc/systemd/system/gps-llama-server@alx.service
sudo cp gps-newsletter.service    /etc/systemd/system/gps-newsletter@alx.service
sudo cp gps-newsletter.timer      /etc/systemd/system/gps-newsletter@alx.timer
sudo cp gps-telegram-bot.service  /etc/systemd/system/gps-telegram-bot@alx.service
sudo cp gps-web-profile.service   /etc/systemd/system/gps-web-profile@alx.service
sudo cp gps-web-profile.timer     /etc/systemd/system/gps-web-profile@alx.timer
sudo systemctl daemon-reload
```

## 2. Start the llama.cpp server

```bash
sudo systemctl enable --now gps-llama-server@alx.service
sudo systemctl status gps-llama-server@alx.service
```

The server selects the best available model from `~/Documents/models/llm/` in priority order:

| Priority | Model file | Status |
|---|---|---|
| 1 | `qwen2.5-7b-instruct-q4_k_m.gguf` | drop in when available → auto-selected on restart |
| 2 | `Qwen3.5-9B.Q4_K_M.gguf` | current active model |
| 3 | `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` | fallback |

To switch models: drop the file in `~/Documents/models/llm/` and restart the service.

```bash
sudo systemctl restart gps-llama-server@alx.service
journalctl -u gps-llama-server@alx.service -n 5  # confirm which model loaded
```

## 3. Start the Telegram bot (persistent callback handler)

```bash
sudo systemctl enable --now gps-telegram-bot@alx.service
sudo systemctl status gps-telegram-bot@alx.service
```

## 4. Enable the daily pipeline timer

```bash
sudo systemctl enable --now gps-newsletter@alx.timer
sudo systemctl list-timers gps-newsletter@alx.timer
```

## 5. Enable the weekly web profile enrichment timer

Requires the `lequartier-searxng` Docker container to be running on `127.0.0.1:8888`.

```bash
sudo systemctl enable --now gps-web-profile@alx.timer
sudo systemctl list-timers gps-web-profile@alx.timer
```

Run once manually to populate LinkedIn + website + description for all 69 companies:

```bash
uv run scripts/france_project_newsletter/enrich_web.py --mode profile
```

## 6. Run once manually to verify

```bash
sudo systemctl start gps-newsletter@alx.service
journalctl -u gps-newsletter@alx.service -f
```

## Required `.env` entries

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
CHANGEDETECTION_API_KEY=...
CHANGEDETECTION_BASE_URL=http://lamai270:5008
LLAMA_CPP_URL=http://127.0.0.1:8181
PAPPERS_API_KEY=...          # optional, for Pappers enrichment
SEARXNG_URL=http://127.0.0.1:8888  # optional, default value shown
```

## Run classify/extract manually against local server

```bash
# Test classification against the running server
uv run scripts/france_project_newsletter/classify.py --llm-url http://127.0.0.1:8080

# Test finance extraction
uv run scripts/france_project_newsletter/extract_finance.py --llm-url http://127.0.0.1:8080
```
