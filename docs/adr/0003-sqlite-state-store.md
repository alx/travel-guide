# SQLite as the GPS Newsletter state store

The GPS Newsletter pipeline accumulates state across daily runs: seen-item hashes, finance model data (capex ledger, project stages, subsidies, employment), pending review queues, and company enrichment. We use a single SQLite file at `data/france_project_newsletter/state.db`, committed to the repo by the daily lamai270 cron job.

The alternative was git-committed JSON sidecars (one file per company). Sidecars work for per-company detail pages but cannot answer cross-company queries (e.g., "all projects in `under_construction` in Île-de-France with > €100M announced capex") without loading the full corpus into memory. SQLite makes these queries trivial and is the natural backend for the future dashboard and API. It is a single binary file, requires no server, and fits cleanly into the existing git-commit-back pattern used by the CI pipeline.

## Considered options

- **JSON sidecars** — simple, human-readable, diffable in git. Rejected because cross-company queries and the finance model's accumulation semantics are awkward without SQL.
- **Postgres** — full query power, concurrent writes. Rejected as premature: the pipeline has one writer (lamai270 cron), and running a Postgres server for a single-user internal tool adds operational overhead with no benefit at this stage.
