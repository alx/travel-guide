# Human-in-the-loop structured extraction for ReventeTicketsFR

r/ReventeTicketsFR VENTE posts are freeform French prose — no enforced title template. Extracting structured fields (venue, artist, date, price, ticket count, seat category) reliably requires either an LLM or a human. The local Qwen model runs on lamai270 and is reachable by the pipeline, but French concert-ticket vocabulary (venue names, flair conventions, price formats) is a narrow domain where LLM extraction errors are silent and map pins silently land in the wrong city. We chose human-in-the-loop via Telegram: the bot sends the raw post, the owner replies with key-value lines, the bot echoes back a formatted summary for confirmation before writing.

## Considered options

- **LLM extraction (Qwen 7B on lamai270)** — fully automatic, no human round-trip. Rejected: silent geocoding errors (wrong city for ambiguous venue names) pollute the map with no feedback loop; the confirmation echo would be needed anyway to catch hallucinations, so the human is already in the loop at that point.
- **Anthropic API extraction** — higher accuracy than local Qwen. Rejected: adds per-post API cost for a low-volume pipeline (~5–20 posts/day) where a human reply takes 30 seconds and produces ground-truth data.
- **Regex template matching** — fast, zero dependencies. Rejected: posts are genuinely freeform; a regex would miss >50% of posts and provide false confidence on the ones it matches.
