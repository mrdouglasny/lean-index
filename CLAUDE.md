# lean-index

Cross-repository index of Lean 4 declarations, filterable by mathematical topic.

## Architecture

- `leanindex/db.py` — SQLite + FTS5 database (`IndexDB` class)
- `leanindex/cli.py` — Click CLI commands
- `leanindex/search.py` — Search orchestration (delegates to `IndexDB`)
- `leanindex/extract/` — Declaration extraction (regex, mathlib cache, lean4export)
- `leanindex/match.py` — Topic matching engine
- `leanindex/update.py` — Orchestration for full update cycle

## Testing

```bash
# Run all fixture-based tests (no external DB needed)
python3 -m pytest tests/test_db.py -v

# Also run smoke tests against a real populated database
LEAN_INDEX_TEST_DB=/path/to/index.db python3 -m pytest tests/test_db.py -v
```

Tests cover: `fts_search`, `structured_search`, `stats`, `changelog` — including filter combinations, edge cases, and field validation.

## Known Constraints

- FTS5 `bm25()` function cannot be used with JOINs or GROUP BY. Use `fts.rank` column instead (equivalent, works in any query context).
- `ln()` is not available in stock SQLite. Avoid in ORDER BY expressions.
- FTS5 table uses `content='declarations'` (content-sync mode) with triggers for insert/update/delete.
