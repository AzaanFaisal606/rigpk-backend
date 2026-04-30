# scripts/

## migrations/

One-shot migration scripts. **Do not re-run these.** They were executed once to backfill data into the DB and are kept for historical reference only.

- `enrich_specs.py` — backfilled spec extraction into existing parts rows
- `migrate_add_specs.py` — added the `specs` column and ran initial extraction
