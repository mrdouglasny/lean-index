# Search and Ranking

lean-index returns the best results first using a composite ranking system. By default, searches return the **top 10** results. This page explains what gets indexed, how results are ranked, and how to control output.

## What Gets Indexed

### Sorry filtering

Declarations whose body contains `sorry` are **excluded** from the index entirely. A `sorry` means the proof or definition is incomplete — these are work-in-progress and not useful as search results.

This applies to all declaration kinds extracted via regex (theorems, lemmas, defs, etc.). Mathlib declarations come from the official docs cache, which only includes compiled declarations (no sorries by construction).

### Extraction sources

| Source | Method | Sorry filtering |
|--------|--------|----------------|
| **Mathlib** | Docs cache (HTTP download) | N/A (compiled, no sorries) |
| **Other repos** | Regex parsing of `.lean` source | Yes, body scanned for `sorry` |
| **Local repos** | Same regex parsing | Yes |

## Ranking

Search results are ranked by a composite score that combines multiple signals. The goal is to surface the most relevant, authoritative, and complete results first.

### Signals

| Signal | Weight | What it measures |
|--------|--------|-----------------|
| **BM25 text relevance** | Primary | How well the query matches the declaration name, type signature, docstring, and module |
| **Topic match confidence** | 0.3 | How the declaration was matched to its topic: module prefix (1.0) > type mention (0.8) > name pattern (0.6) |
| **Repository stars** | 0.2 (log-scaled) | Community trust signal — `ln(stars + 1)` so diminishing returns |
| **Has docstring** | 0.1 | Documented declarations are more useful than undocumented ones |
| **Declaration kind** | 0.1–0.3 | Theorems and lemmas (0.3) > defs, structures, classes (0.2) > abbrevs (0.1) > instances (0) |

For text searches, BM25 is the dominant signal — the other signals act as tiebreakers between equally relevant text matches. For non-text queries (e.g., `--kind theorem --topic lie-algebras`), only the non-BM25 signals apply.

### Examples

A search for `"Killing form"` ranks results roughly like this:

1. `killingForm` (def, Mathlib, 173K stars, has docstring, module-prefix topic match) — highest
2. `killingForm_eq_traceForm` (theorem, Mathlib, docstring) — high
3. `KillingForm.something` (theorem, 5-star independent repo, no docstring) — medium
4. `my_killing_helper` (instance, 0-star repo, name-pattern match only) — low

## Controlling Output

### Result count

```bash
# Default: top 10 results
lean-index search "Killing form"

# Custom limit
lean-index search "Killing form" -n 25

# All results
lean-index search "Killing form" --all
```

### Filters

Filters narrow the search space before ranking. They can be combined freely.

```bash
# By declaration kind
lean-index search "nilpotent" --kind theorem
lean-index search --kind def --kind structure  # (one kind at a time)

# By topic
lean-index search "weight" --topic representation-theory

# By repository
lean-index search "Cartan" --repo mathlib
lean-index search --repo PhysLean

# By type signature
lean-index search --type LieAlgebra
lean-index search --type "RootSystem"

# By date (first seen in index)
lean-index search --since 2026-02-01
lean-index search --kind theorem --since 2026-01-01 --topic lie-algebras

# Combine everything
lean-index search "semisimple" --kind theorem --topic lie-algebras --repo mathlib -n 20
```

### Output formats

```bash
# Human-readable table (default)
lean-index search "Killing form"

# JSON (for scripting)
lean-index search "Killing form" --json
lean-index search --kind theorem --topic lie-algebras --json | python3 -m json.tool
```

### No-query browsing

Omit the query to browse by filters alone:

```bash
# All theorems in a topic, ranked by quality signals
lean-index search --kind theorem --topic root-systems

# Everything added this week
lean-index search --since 2026-02-17

# All declarations from a specific repo
lean-index search --repo VirasoroProject --all
```

## Adding Local Repos

You can add your own repos (local directories or remote URLs) to supplement the pre-built index:

```bash
# Add a local project
lean-index add ~/Documents/Github/my-project/lean

# Add a remote repo
lean-index add https://github.com/someone/their-repo

# Re-index all local repos
lean-index update
```

Local repos are saved in `local-repos.yaml` and re-indexed on each `lean-index update`. Declarations with `sorry` are filtered out the same way as for online repos.

If a local repo URL also appears in the online index, the online version takes precedence (no duplicates).
