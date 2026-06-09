#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["rich"]
# ///

import csv
import re
import urllib.parse
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

SCRIPT_DIR = Path(__file__).parent
VENUES_CSV = SCRIPT_DIR / "venues.csv"
UNMATCHED_PATH = SCRIPT_DIR / "unmatched-venues.txt"
NOMATCH_CSV = SCRIPT_DIR / "venues_nomatch.csv"

VALID_CATEGORIES = [
    "Concert Bar",
    "Radio",
    "Record Shop",
    "Cinema",
    "Studio",
    "Boutique",
    "Merch/Print",
    "Theater",
    "Other",
]

VENUES_FIELDNAMES = ["name", "display_name", "address", "category", "logo", "url"]


# Verbatim copy of generate.py:149-161 — must stay in sync
def normalize_venue_name(name: str) -> str:
    import unicodedata
    name = name.lower().strip()
    name = name.replace("‘", "'").replace("’", "'").replace("ʼ", "'")
    name = "".join(
        c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c)
    )
    for prefix in ("le ", "la ", "l'", "les ", "au ", "aux ", "the "):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name


def load_known_names() -> set[str]:
    if not VENUES_CSV.exists():
        return set()
    with VENUES_CSV.open(encoding="utf-8") as f:
        return {normalize_venue_name(row["name"]) for row in csv.DictReader(f)}


def load_all_venues() -> list[dict]:
    if not VENUES_CSV.exists():
        return []
    with VENUES_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_nomatch_names() -> set[str]:
    if not NOMATCH_CSV.exists():
        return set()
    with NOMATCH_CSV.open(encoding="utf-8") as f:
        return {row["name"] for row in csv.DictReader(f)}


def load_unmatched() -> list[str]:
    if not UNMATCHED_PATH.exists():
        return []
    return [line for line in UNMATCHED_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def expand_gmaps_url(url: str) -> tuple[str, str | None]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.url
    except Exception:
        return url, None
    m = re.search(r"/maps/place/([^/@?]+)", final_url)
    if m:
        return final_url, urllib.parse.unquote_plus(m.group(1)).strip()
    return final_url, None


def append_venue(display_name: str, address: str, category: str, url: str) -> None:
    name = normalize_venue_name(display_name)
    row = {"name": name, "display_name": display_name, "address": address, "category": category, "logo": "", "url": url}
    write_header = not VENUES_CSV.exists()
    with VENUES_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=VENUES_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def append_nomatch(raw_name: str) -> None:
    write_header = not NOMATCH_CSV.exists()
    with NOMATCH_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name"])
        if write_header:
            writer.writeheader()
        writer.writerow({"name": raw_name})


def prompt_category(console: Console) -> str:
    table = Table(show_header=False, box=None, padding=(0, 2))
    for i, cat in enumerate(VALID_CATEGORIES, 1):
        table.add_row(f"[bold cyan]{i}[/]", cat)
    console.print(table)
    while True:
        choice = Prompt.ask("Category number")
        if choice.isdigit() and 1 <= int(choice) <= len(VALID_CATEGORIES):
            return VALID_CATEGORIES[int(choice) - 1]
        console.print("[red]Invalid — enter a number from the list.[/]")


def handle_merge(raw_name: str, console: Console) -> None:
    venues = load_all_venues()
    if not venues:
        console.print("[red]venues.csv is empty.[/]")
        return

    while True:
        query = Prompt.ask("Search existing venue")
        q = normalize_venue_name(query)
        matches = [v for v in venues if q in normalize_venue_name(v["display_name"])][:10]
        if not matches:
            console.print("[dim]No matches, try again.[/]")
            continue

        table = Table(show_header=True, box=None, padding=(0, 2))
        table.add_column("#", style="bold cyan", width=3)
        table.add_column("Name")
        table.add_column("Category")
        table.add_column("Address")
        for i, v in enumerate(matches, 1):
            table.add_row(str(i), v["display_name"], v["category"], v["address"])
        console.print(table)

        choice = Prompt.ask("Select number (blank to search again)", default="")
        if not choice:
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(matches):
            selected = matches[int(choice) - 1]
            break
        console.print("[red]Invalid choice.[/]")

    new_row = dict(selected)
    new_row["name"] = normalize_venue_name(raw_name)
    with VENUES_CSV.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=VENUES_FIELDNAMES).writerow(new_row)

    console.print(Panel(
        f"[green]Merged:[/] [bold]{raw_name}[/] → [bold]{selected['display_name']}[/]",
        title="Alias added to venues.csv",
        border_style="green",
    ))


def handle_add(raw_name: str, console: Console) -> None:
    gmaps_url = Prompt.ask("GMaps URL (optional)", default="")
    prefill = raw_name

    if gmaps_url.strip():
        console.print("  [dim]Expanding URL…[/]")
        _, extracted = expand_gmaps_url(gmaps_url.strip())
        if extracted:
            prefill = extracted
            console.print(f"  [dim]Extracted: {extracted}[/]")

    display_name = Prompt.ask("display_name", default=prefill)
    address = Prompt.ask("address")
    category = prompt_category(console)
    url = Prompt.ask("url (optional)", default="")

    append_venue(display_name, address, category, url.strip())
    console.print(Panel(
        f"[green]Added:[/] [bold]{display_name}[/] ({category})\n{address}",
        title="Saved to venues.csv",
        border_style="green",
    ))


def main() -> None:
    console = Console()

    unmatched = load_unmatched()
    known = load_known_names()
    nomatch = load_nomatch_names()

    pending = [
        v for v in unmatched
        if normalize_venue_name(v) not in known and v not in nomatch
    ]

    already_done = len(unmatched) - len(pending)
    console.print(Panel(
        f"[bold]{len(pending)}[/] venues to classify"
        + (f"  [dim]({already_done} already done)[/]" if already_done else ""),
        title="Toulouse Distorama — Venue Classifier",
        border_style="cyan",
    ))

    for idx, raw_name in enumerate(pending, 1):
        console.rule(f"[bold cyan]{idx} / {len(pending)}[/]")
        console.print(Panel(f"[bold yellow]{raw_name}[/]", title="Unmatched venue"))

        action = Prompt.ask(
            "[A]dd new / [M]erge with existing / [N]o-match / [S]kip / [Q]uit",
            choices=["a", "m", "n", "s", "q"],
            default="s",
        ).lower()

        if action == "q":
            console.print("[yellow]Quitting.[/]")
            break
        elif action == "s":
            console.print("[dim]Skipped.[/]")
        elif action == "n":
            append_nomatch(raw_name)
            console.print(f"[dim]Saved to {NOMATCH_CSV.name}.[/]")
        elif action == "a":
            handle_add(raw_name, console)
        elif action == "m":
            handle_merge(raw_name, console)
    else:
        console.print(Panel("[bold green]All venues classified![/]", border_style="green"))


if __name__ == "__main__":
    main()
