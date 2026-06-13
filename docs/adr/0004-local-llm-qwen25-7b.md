# Local llama.cpp + Qwen2.5 7B Instruct as the AI backend

All AI pipeline calls (relevance filter, signal classifier) run against a llama.cpp server on lamai270's RTX 4060 (8GB VRAM) using Qwen2.5 7B Instruct Q4_K_M (~5.4GB VRAM). The pipeline checks server availability before each run; if unreachable, items are saved with `pending_classification = true` and reclassified on the next available run.

The alternative was the Claude API (Haiku for relevance filtering, Sonnet for classification). The local approach costs €0/day at the expense of availability — if the GPU is busy or the machine is off, classification is deferred rather than skipped. Qwen2.5 7B was chosen over Llama 3.1 8B for its stronger French-language coverage and more reliable structured JSON output via llama.cpp grammar constraints, both critical for French industrial news classification. The degraded-mode design means no data is ever lost: raw items always land in SQLite regardless of AI availability.

## Consequences

- AI classification accuracy is lower than frontier models. This is acceptable for the internal phase; the finance review queue (human approval before ledger writes) is a deliberate safety net for the most consequential extractions.
- Phase 2 (summarization, weekly narrative) will revisit this choice — longer-form generation may warrant a larger local model or a selective Claude API call.
