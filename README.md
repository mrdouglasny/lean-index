# lean-index

A cross-repository index of Lean 4 declarations, filterable by mathematical topic.

## Problem

Lean 4 formalizations are spread across 565+ packages on [Lean Reservoir](https://reservoir.lean-lang.org) and many more on GitHub. Mathlib alone has 384K+ declarations, but valuable work also lives in independent repos. There is no unified way to:

- Search for declarations across all Lean repos by mathematical topic
- Track when new formalizations appear that are relevant to your work
- Get notified when Mathlib adds theorems in your area of interest

## How It Works

lean-index maintains a SQLite database of Lean 4 declarations gathered from three sources:

1. **Mathlib declaration cache** — downloaded from the official docs site (~384K declarations, updated every 8 hours)
2. **Lean Reservoir packages** — 565+ registered Lean 4 packages, indexed by parsing `.lean` source files
3. **GitHub search** — additional Lean 4 repos discovered via topic-specific keyword search

Declarations are matched to configurable **topics** (e.g., "Lie algebras", "root systems") using module prefixes, type signature mentions, and name patterns.

## Three-Layer Architecture

```
lean-index                 pip package — the engine (this repo)
    |
    v
lean-index-lie             topic repo — config + CI builds DB + publishes as GitHub release
lean-index-qft             topic repo — same pattern, different topic
    |
    v
auto-lie                   consumer project — pip installs lean-index,
other-lie-project            downloads pre-built DB from topic repo
```

**lean-index** (this repo) is the engine: extraction, indexing, search, updates. Install it with pip.

**Topic repos** (e.g., `lean-index-lie`) define topic configs and curated repo lists. Their CI builds the database weekly and publishes it as a GitHub release. Anyone can create a topic repo for their area of interest.

**Consumer projects** (e.g., `auto-lie`) install lean-index and download a pre-built database from a topic repo. No need to rebuild the index — just fetch and search.

## Quick Start

### As a consumer (use an existing topical index)

```bash
# Install the engine
pip install git+https://github.com/mrdouglasny/lean-index.git

# Download a pre-built topic database
lean-index fetch-db mrdouglasny/lean-index-lie

# Search
lean-index search "Killing form"
lean-index search --kind theorem --topic lie-algebras
lean-index search --type LieAlgebra --since 2026-01-01

# Reports
lean-index stats
lean-index changelog --since 2026-02-01
lean-index repos
```

### As a topic maintainer (create a new topical index)

```bash
# Create a new topic repo
mkdir lean-index-qft && cd lean-index-qft
pip install git+https://github.com/mrdouglasny/lean-index.git

# Define your topics
cat > topics.yaml << 'EOF'
topics:
  - name: operator-algebras
    search_keywords: ["C*-algebra", "von Neumann algebra", "operator algebra"]
    matchers:
      module_prefixes:
        - "Mathlib.Analysis.CStarAlgebra"
        - "Mathlib.Analysis.VonNeumannAlgebra"
      type_mentions:
        - "CStarAlgebra"
        - "VonNeumannAlgebra"
        - "Spectrum"
  - name: functional-analysis
    search_keywords: ["Hilbert space", "Banach space", "spectral theory"]
    matchers:
      module_prefixes:
        - "Mathlib.Analysis.InnerProductSpace."
        - "Mathlib.Analysis.NormedSpace."
      type_mentions:
        - "InnerProductSpace"
        - "NormedSpace"
        - "ContinuousLinearMap"
EOF

# Build the index
lean-index init
lean-index update

# Search your topic
lean-index search "spectral theorem" --topic functional-analysis
```

## Topic Configuration

### topics.yaml

Defines what mathematical topics to track and how to match declarations:

```yaml
topics:
  - name: lie-algebras
    search_keywords: ["Lie algebra", "Cartan subalgebra", "semisimple"]
    matchers:
      module_prefixes:
        - "Mathlib.Algebra.Lie."
        - "Mathlib.Geometry.Manifold.Algebra.LieGroup"
      type_mentions:
        - "LieAlgebra"
        - "LieGroup"
        - "LieSubalgebra"
      name_patterns:
        - ".*[Ll]ie.*"
        - ".*[Cc]artan.*"

  - name: root-systems
    search_keywords: ["root system", "Dynkin diagram", "Weyl group"]
    matchers:
      module_prefixes:
        - "Mathlib.LinearAlgebra.RootSystem."
        - "Mathlib.GroupTheory.Coxeter."
      type_mentions:
        - "RootSystem"
        - "RootPairing"
        - "CoxeterMatrix"
```

### repos.yaml (optional)

Curated repos to track beyond what's auto-discovered:

```yaml
repos:
  - url: https://github.com/ocfnash/LieClassification
    description: "Classification of finite-dimensional Lie algebras"
  - url: https://github.com/LieLean/LowDimSolvClassification
    description: "Low-dimensional solvable Lie algebra classification"
```

## Extraction Modes

| Mode | Speed | Requires Build | Type Signatures |
|------|-------|---------------|----------------|
| **Mathlib cache** | Fast (HTTP download) | No | HTML (converted to text) |
| **Regex** (default) | Fast (source parsing) | No | Heuristic from source |
| **lean4export** (optional) | Slow (lake build) | Yes | Exact elaborated types |

Most users only need the first two. Use `lean-index build-repo <url>` for deep extraction when exact type signatures matter.

## Publishing a Topical Index

Topic repos use GitHub Actions to build and publish the database weekly:

```yaml
# .github/workflows/update.yml
name: Update Index
on:
  schedule:
    - cron: '0 6 * * 1'  # Weekly Monday 6 AM UTC
  workflow_dispatch:

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install git+https://github.com/mrdouglasny/lean-index.git
      - run: lean-index init && lean-index update
      - run: lean-index stats > STATS.md
      - run: lean-index changelog --since $(date -d '7 days ago' +%Y-%m-%d) > CHANGELOG.md
      - name: Publish database as release
        run: |
          gh release create "$(date +%Y-%m-%d)" data/index.db \
            --title "Index $(date +%Y-%m-%d)" \
            --notes "$(cat CHANGELOG.md)"
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Consumers download the latest release with:

```bash
lean-index fetch-db mrdouglasny/lean-index-lie
```

## Database

SQLite with FTS5 full-text search. Key tables:

- **repos** — tracked repositories with last-indexed commit SHA
- **declarations** — name, kind, type signature, docstring, module, file, line
- **topics** / **topic_matches** — which declarations match which topics
- **repo_modules** — module import graph
- **update_log** — history of index updates

## CLI Reference

```bash
# Setup
lean-index init                          # Create DB + download Mathlib cache
lean-index fetch-db <owner/repo>         # Download pre-built DB from topic repo

# Indexing (for topic maintainers)
lean-index update                        # Full cycle: discover + index + match
lean-index discover                      # Find repos from Reservoir + GitHub
lean-index index-mathlib                 # Re-download + reindex Mathlib cache
lean-index index-repo <url>              # Index specific repo (regex)
lean-index build-repo <url>              # Deep index via lake build (optional)
lean-index add-repo <url>                # Add to curated list

# Search
lean-index search <query>               # Full-text search
lean-index search --kind theorem         # Filter by declaration kind
lean-index search --topic lie-algebras   # Filter by topic
lean-index search --since 2026-02-01     # Filter by first-seen date
lean-index search --type LieAlgebra      # Filter by type signature mention
lean-index search --repo mathlib         # Filter by repo
lean-index search --json                 # Output as JSON

# Reports
lean-index stats                         # Summary statistics
lean-index changelog --since 2026-02-15  # What's new
lean-index repos                         # List tracked repos
```

## Package Structure

```
lean-index/              # This repo
  leanindex/
    cli.py               # Click CLI
    config.py            # Topic config loader
    db.py                # SQLite + FTS5
    discover.py          # Reservoir + GitHub discovery
    extract/
      regex.py           # Parse .lean without building
      mathlib_cache.py   # Mathlib declaration cache
      lean4export.py     # Optional deep extraction
    match.py             # Topic matching engine
    search.py            # Full-text + structured search
    update.py            # Orchestration
    report.py            # Stats + changelogs
```

## Installation

```bash
pip install git+https://github.com/mrdouglasny/lean-index.git
```

Requires Python 3.10+. Dependencies: click, pyyaml, requests, beautifulsoup4.

## License

MIT
