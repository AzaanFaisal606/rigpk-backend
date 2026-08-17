This directory holds `ppc.db`. Run `python run_all.py` to generate it (scrapes all retailers and populates the DB).

This local file is only used when `TURSO_DATABASE_URL` is unset. When it's set (as `.env` does by
default in this repo, and as CI/production always do), every command — scrapers, API server,
tests — reads and writes the hosted Turso database instead, and nothing here is touched. Unset
`TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` to force local SQLite mode.
