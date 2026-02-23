# Using a Topical Index in Your Project

Download a pre-built topical index and search it from your project. No need to rebuild anything — the topic repo's CI does that for you.

## Prerequisites

- Python 3.10+
- `gh` CLI installed and authenticated (`gh auth login`)

## Step 1: Install lean-index

```bash
pip install git+https://github.com/mrdouglasny/lean-index.git
```

## Step 2: Download a topical index

Find a topic repo that covers your area of interest:

| Topic | Repo |
|-------|------|
| Lie theory | `mrdouglasny/lean-index-lie` |
| Constructive QFT | `mrdouglasny/lean-index-qft` |

Download the latest published database:

```bash
lean-index fetch-db mrdouglasny/lean-index-lie
```

This downloads `index.db` from the topic repo's latest GitHub release into `./data/index.db`.

To put it somewhere specific:

```bash
lean-index fetch-db mrdouglasny/lean-index-lie --data-dir /path/to/your/data
```

## Step 3: Search

```bash
# Full-text search
lean-index search "Killing form"
lean-index search "Verma module"

# Filter by declaration kind
lean-index search --kind theorem "semisimple"
lean-index search --kind def "root system"
lean-index search --kind structure "Cartan"

# Filter by topic (topics defined by the topic repo)
lean-index search --topic lie-algebras "nilpotent"
lean-index search --topic root-systems "Weyl"

# Filter by repo
lean-index search --repo mathlib "LieAlgebra"
lean-index search --repo ocfnash/LieClassification

# What's new since a date
lean-index search --since 2026-02-01
lean-index search --kind theorem --since 2026-01-01 --topic lie-algebras

# Type signature search (find declarations mentioning a type)
lean-index search --type LieAlgebra
lean-index search --type "RootSystem"

# JSON output (for scripting)
lean-index search "Killing" --json | python3 -m json.tool
```

## Step 4: Explore

```bash
# Summary statistics: how many declarations, by kind, by topic, by repo
lean-index stats

# What's changed recently
lean-index changelog --since 2026-02-01

# List all tracked repos
lean-index repos
```

## Keeping up to date

The topic repo publishes a new database weekly (or whenever its maintainer triggers a build). To get the latest:

```bash
lean-index fetch-db mrdouglasny/lean-index-lie
```

This replaces your local `data/index.db` with the latest release. Your search results will reflect any new Mathlib commits, new repos discovered, and updated declarations.

## Using multiple topical indexes

You can download indexes from multiple topic repos. They merge into the same database:

```bash
lean-index fetch-db mrdouglasny/lean-index-lie
lean-index fetch-db mrdouglasny/lean-index-qft
```

Or if the topics are in separate databases, specify which to use:

```bash
lean-index search "operator algebra" --data-dir data/qft
lean-index search "Lie algebra" --data-dir data/lie
```

## Integrating with your project

### Add to your project's setup

In your project's README or Makefile:

```makefile
# Makefile
setup-index:
	pip install git+https://github.com/mrdouglasny/lean-index.git
	lean-index fetch-db mrdouglasny/lean-index-lie --data-dir data/lean-index

search:
	lean-index search $(Q) --data-dir data/lean-index
```

### Add to .gitignore

```
data/lean-index/
```

### Use from Python

```python
import subprocess
import json

def search_lean_index(query, kind=None, topic=None):
    """Search the Lean declaration index."""
    cmd = ["lean-index", "search", query, "--json"]
    if kind:
        cmd.extend(["--kind", kind])
    if topic:
        cmd.extend(["--topic", topic])
    result = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(result.stdout)

# Find all Lie algebra theorems added this month
results = search_lean_index("", kind="theorem", topic="lie-algebras")
for r in results:
    print(f"[{r['kind']}] {r['name']}  ({r['repo']})")
```

## Example: checking if something is already formalized

Before starting a new formalization, check if it already exists somewhere:

```bash
# Is Engel's theorem formalized?
$ lean-index search "Engel" --kind theorem
[theorem] LieAlgebra.isNilpotent_of_forall_isNilpotent  (mathlib)
           "Engel's theorem: if all elements act nilpotently, the algebra is nilpotent"

# Is there a Verma module definition anywhere?
$ lean-index search "Verma" --kind def
(no results — not formalized yet)

# What root system content exists outside Mathlib?
$ lean-index search --topic root-systems --repo "!mathlib"
[def] RootSystem.classify  (ocfnash/LieClassification)
```

## Troubleshooting

**"No database found"** — Run `lean-index fetch-db <owner/repo>` first.

**"No releases found"** — The topic repo hasn't published a release yet. Ask the maintainer to trigger the CI workflow, or build locally:
```bash
git clone https://github.com/owner/lean-index-topic.git
cd lean-index-topic
lean-index init && lean-index update
```

**Stale results** — Re-run `lean-index fetch-db` to get the latest weekly build.
