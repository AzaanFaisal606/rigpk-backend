# scripts/

## check_db_integrity.py

Operational DB integrity check — run against whatever `TURSO_DATABASE_URL` (or local `DB_PATH`)
currently points at, including production. Read-only (opens with `allow_remote_migrations=False`,
never runs schema/migrations). Companion to `tests/test_db_integrity.py`, which checks
schema/write-path invariants against an isolated tmp DB and never touches the network.

```
python scripts/check_db_integrity.py
```

## sanitize_log.py

Neutralises instruction-shaped text (HTML comments, tag-shaped runs, "ignore previous
instructions" phrasing, overlong lines) in a scrape log before it's handed to the self-heal
action, which reads the log and holds repo write. Not a complete prompt-injection defence — just
removes the cheap vectors. `python -m scripts.sanitize_log < raw.log > safe.log`

## save_fixture.py

Saves a retailer page as a test fixture, fetched through the project's own scraper UA/headers so
the saved page matches exactly what a scraper sees. `python scripts/save_fixture.py <url>
<fixture-name.html>`

## notify_discord.py

Posts a scrape summary embed to Discord after a CI run, reading `scrape_runs` + current DB
totals. Exits 0 (warns only) if `DISCORD_WEBHOOK_URL` is unset — a missing webhook must never
fail the scrape job.

## migrations/

One-shot migration scripts, each idempotent unless noted — safe to re-run those, but they exist
to be run once per environment (local, then production) after their corresponding schema/behavior
change lands, not on a schedule.

**Safe to re-run (idempotent):**
- `2026_08_07_name_norm_backfill.py` — fills `parts.name_norm` / `prebuilts.name_norm` for rows
  written before the column existed; only touches `NULL` rows
- `2026_08_07_price_log_latest_index.py` — creates `idx_price_log_latest` via a standalone
  `execute()`; libSQL was silently skipping the `CREATE INDEX … DESC` inside `executescript()`
- `2026_08_14_latest_price_backfill.py` — backfills `parts.latest_price` from `price_log`,
  verifies zero divergence by read-back
- `2026_08_14_respec_backfill.py` — re-runs `extract_specs` over every part and rewrites
  `parts.specs`; only issues an `UPDATE` where the recomputed dict actually differs
- `backfill_models_and_trends.py` — re-extracts GPU/CPU `model` specs, then rebuilds
  `price_trends` from all historical `price_log` rows (rebuild is itself idempotent: `DELETE` +
  full recompute)

**One-shot, kept for historical reference only — do not re-run:**
- `enrich_specs.py` — backfilled spec extraction into existing parts rows
- `migrate_add_specs.py` — added the `specs` column and ran initial extraction

`_guard.py` is not a migration itself — every script above calls its `resolve_target()` first,
because `.env` sets `TURSO_DATABASE_URL` locally, so a bare `python scripts/migrations/foo.py`
targets hosted production by default unless something stops it.
