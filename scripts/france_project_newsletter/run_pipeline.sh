#!/usr/bin/env bash
# Daily GPS Newsletter pipeline — runs on lamai270.
# Invoked by systemd timer. Exits 0 always; errors are logged, not fatal.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT_DIR="$REPO/scripts/france_project_newsletter"
LOG_DIR="$REPO/logs"
LOG_FILE="$LOG_DIR/gps_newsletter_$(date +%Y%m%d).log"

mkdir -p "$LOG_DIR"
exec >> "$LOG_FILE" 2>&1

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) GPS Newsletter pipeline start ==="

cd "$REPO"

# 1. Ensure DB schema is current (idempotent)
uv run "$SCRIPT_DIR/init_db.py"

# 2. Fetch new items from RSS + changedetection.io
uv run "$SCRIPT_DIR/fetch_digest.py" --period daily

# 3. AI classification (skips gracefully if llama.cpp server is down)
uv run "$SCRIPT_DIR/classify.py"

# 4. Finance extraction (skips gracefully if llama.cpp server is down)
uv run "$SCRIPT_DIR/extract_finance.py"

# 5. Telegram: push high-priority alerts + finance review prompts
uv run "$SCRIPT_DIR/telegram_bot.py" push

# 6. Telegram: daily summary of non-priority items
uv run "$SCRIPT_DIR/telegram_bot.py" summary

# 7. Export digest JSON for Hugo build (backward compat with GitHub Actions)
python3 - <<'EOF'
import json, sqlite3, pathlib
from datetime import datetime, timezone

DB = pathlib.Path("data/france_project_newsletter/state.db")
OUT_DIR = pathlib.Path("data/france_project_newsletter")
OUT_DIR.mkdir(parents=True, exist_ok=True)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT * FROM news_items WHERE relevant=1 AND pending_classification=0 ORDER BY date DESC LIMIT 200"
).fetchall()
items = [dict(r) for r in rows]
con.close()

digest = {
    "generated": datetime.now(timezone.utc).isoformat(),
    "period": "daily",
    "total_items": len(items),
    "high_priority_count": sum(1 for i in items if i.get("signal_type") in
        {"funding_round","groundbreaking","production_start","M&A"}),
    "items": items,
}
date_str = datetime.now().strftime("%Y%m%d")
out = OUT_DIR / f"digest_{date_str}.json"
out.write_text(json.dumps(digest, ensure_ascii=False, indent=2))
print(f"Digest JSON written: {out}")
EOF

# 8. Commit digest JSON + SQLite state and push to trigger Hugo build
git -C "$REPO" add data/france_project_newsletter/
if ! git -C "$REPO" diff --cached --quiet; then
    git -C "$REPO" commit -m "chore(gps-newsletter): daily digest $(date +%Y-%m-%d) [pipeline]"
    git -C "$REPO" push
    echo "Committed and pushed."
else
    echo "Nothing new to commit."
fi

echo "=== Pipeline complete ==="
