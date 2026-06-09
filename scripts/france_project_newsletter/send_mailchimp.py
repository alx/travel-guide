#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["mailchimp-marketing", "jinja2"]
# ///
"""
Render newsletter template and send via Mailchimp.

Usage:
  uv run scripts/france_project_newsletter/send_mailchimp.py [--mode daily|weekly] [--dry-run]

Env vars required:
  MAILCHIMP_API_KEY        — e.g. abc123-us1
  MAILCHIMP_LIST_ID        — Audience/list ID
  MAILCHIMP_SERVER_PREFIX  — e.g. us1
"""

import argparse
import json
import os
import pathlib
import sys

import mailchimp_marketing as MailchimpMarketing
from mailchimp_marketing.api_client import ApiClientError
from jinja2 import Environment, FileSystemLoader

DIGEST_DIR = pathlib.Path(__file__).parents[2] / "data/france_project_newsletter"
TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"
PREVIEW_PATH = DIGEST_DIR / "preview.html"

CATEGORY_ICONS = {
    "Matériaux critiques":   "🔋",
    "Aéronautique & Défense":"✈️",
    "Data Center & IA":      "🖥️",
    "Recyclage":             "♻️",
    "Énergie & Nucléaire":   "⚡",
    "Industrie":             "🏭",
    "Sidérurgie":            "⚙️",
    "Biocarburants":         "🌿",
    "Électromobilité":       "🚗",
    "Agroalimentaire":       "🌾",
}

CATEGORY_COLORS = {
    "Matériaux critiques":   "#3b82f6",
    "Aéronautique & Défense":"#6366f1",
    "Data Center & IA":      "#8b5cf6",
    "Recyclage":             "#10b981",
    "Énergie & Nucléaire":   "#f59e0b",
    "Industrie":             "#64748b",
    "Sidérurgie":            "#78716c",
    "Biocarburants":         "#22c55e",
    "Électromobilité":       "#06b6d4",
    "Agroalimentaire":       "#84cc16",
}


def load_env() -> None:
    env_file = pathlib.Path(__file__).parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def latest_digest() -> pathlib.Path:
    files = sorted(DIGEST_DIR.glob("digest_*.json"), reverse=True)
    if not files:
        sys.exit(f"No digest files found in {DIGEST_DIR}")
    return files[0]


def group_by_category(items: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for item in items:
        cat = item.get("category", "Industrie")
        groups.setdefault(cat, []).append(item)
    return dict(sorted(groups.items()))


def render_html(digest: dict, mode: str) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template_name = "daily_alert.html.j2" if mode == "daily" else "weekly_digest.html.j2"
    template = env.get_template(template_name)

    items = digest["items"]
    if mode == "daily":
        items = [i for i in items if i.get("high_priority")][:5]

    return template.render(
        digest=digest,
        items=items,
        groups=group_by_category(items if mode == "daily" else digest["items"]),
        mode=mode,
        generated=digest["generated"][:10],
        category_icons=CATEGORY_ICONS,
        category_colors=CATEGORY_COLORS,
        map_url="https://maps.girard-davila.net/france-grands-projets-strategiques/",
    )


def send_campaign(html: str, digest: dict, mode: str, server_prefix: str, api_key: str, list_id: str) -> str:
    client = MailchimpMarketing.Client()
    client.set_config({"api_key": api_key, "server": server_prefix})

    date_label = digest["generated"][:10]
    if mode == "daily":
        subject_fr = f"⚡ Alerte Grands Projets – {date_label}"
        subject_en = f"⚡ Strategic Projects Alert – {date_label}"
    else:
        subject_fr = f"🏭 Grands Projets Stratégiques – Semaine du {date_label}"
        subject_en = f"🏭 French Strategic Projects – Week of {date_label}"

    subject = f"{subject_fr} / {subject_en}"

    campaign = client.campaigns.create({
        "type": "regular",
        "recipients": {"list_id": list_id},
        "settings": {
            "subject_line": subject,
            "from_name": "Grands Projets Stratégiques",
            "reply_to": "girard.davila@gmail.com",
            "title": f"Newsletter {mode} {date_label}",
        },
    })

    campaign_id = campaign["id"]

    client.campaigns.set_content(campaign_id, {"html": html})
    client.campaigns.actions.send(campaign_id)

    return campaign_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--dry-run", action="store_true", help="Render HTML preview only, do not send")
    parser.add_argument("--digest", type=str, help="Path to specific digest JSON (default: latest)")
    args = parser.parse_args()

    load_env()

    digest_path = pathlib.Path(args.digest) if args.digest else latest_digest()
    digest = json.loads(digest_path.read_text(encoding="utf-8"))

    if args.mode == "daily" and digest["high_priority_count"] == 0 and not args.dry_run:
        print("No high-priority items today — skipping daily alert.")
        sys.exit(0)

    html = render_html(digest, args.mode)

    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_PATH.write_text(html, encoding="utf-8")
    print(f"HTML preview written to {PREVIEW_PATH}")

    if args.dry_run:
        print("Dry-run mode: not sending to Mailchimp.")
        return

    api_key = os.environ.get("MAILCHIMP_API_KEY")
    list_id = os.environ.get("MAILCHIMP_LIST_ID")
    server_prefix = os.environ.get("MAILCHIMP_SERVER_PREFIX")

    if not all([api_key, list_id, server_prefix]):
        sys.exit("Error: MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_SERVER_PREFIX must all be set")

    try:
        campaign_id = send_campaign(html, digest, args.mode, server_prefix, api_key, list_id)  # type: ignore[arg-type]
        print(f"Campaign sent: {campaign_id}")
    except ApiClientError as exc:
        sys.exit(f"Mailchimp API error: {exc.text}")


if __name__ == "__main__":
    main()
