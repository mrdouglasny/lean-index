# Creating a New Topic Repo

A topic repo defines what mathematical topics you want to track across the Lean 4 ecosystem, builds a searchable index, and publishes it for others to use.

## Prerequisites

- Python 3.10+
- GitHub account (for publishing)
- `gh` CLI installed and authenticated (`gh auth login`)

## Step 1: Install lean-index

```bash
pip install git+https://github.com/mrdouglasny/lean-index.git
```

Verify:

```bash
lean-index --help
```

## Step 2: Create the repo

```bash
mkdir lean-index-mytopic && cd lean-index-mytopic
git init
```

## Step 3: Define your topics

Create `topics.yaml`. Each topic has:
- **name**: identifier used in search filters
- **search_keywords**: used to discover relevant repos on GitHub and Reservoir
- **matchers**: rules for matching Mathlib/Lean declarations to this topic

```yaml
# topics.yaml
topics:
  - name: algebraic-geometry
    search_keywords:
      - "algebraic geometry"
      - "scheme"
      - "sheaf cohomology"
    matchers:
      module_prefixes:
        - "Mathlib.AlgebraicGeometry."
        - "Mathlib.Geometry.RingedSpace."
      type_mentions:
        - "Scheme"
        - "Presheaf"
        - "Sheaf"
        - "LocallyRingedSpace"
      name_patterns:
        - ".*[Ss]cheme.*"
        - ".*[Ss]heaf.*"

  - name: commutative-algebra
    search_keywords:
      - "commutative algebra"
      - "Noetherian ring"
      - "localization"
    matchers:
      module_prefixes:
        - "Mathlib.RingTheory."
        - "Mathlib.Algebra.Module.Localized"
      type_mentions:
        - "IsNoetherianRing"
        - "Localization"
        - "IsLocalRing"
        - "PrimeSpectrum"
```

**Matcher types** (declarations matching any rule are included):

| Matcher | What it checks | Confidence |
|---------|---------------|------------|
| `module_prefixes` | Declaration's module path starts with prefix | 1.0 |
| `type_mentions` | Type signature contains the identifier | 0.8 |
| `name_patterns` | Declaration name matches regex | 0.6 |

You can define multiple closely related topics in one repo (e.g., algebraic geometry + commutative algebra).

## Step 4: Curate repos (optional)

If you know of Lean repos relevant to your topic that might not be auto-discovered, list them in `repos.yaml`:

```yaml
# repos.yaml
repos:
  - url: https://github.com/leanprover-community/mathlib4
    description: "The math library of Lean 4"
    # Mathlib is always indexed via its declaration cache,
    # but listing it here ensures it appears in repo listings.

  - url: https://github.com/someone/lean-schemes
    description: "Formalization of scheme theory in Lean 4"

  - url: https://github.com/someone/etale-cohomology
    description: "Etale cohomology in Lean 4"
    branch: main  # optional, defaults to repo's default branch
```

## Step 5: Build the index

```bash
# Initialize database and download Mathlib declaration cache (~384K declarations)
lean-index init

# Run full update: discover repos, extract declarations, match topics
lean-index update
```

This will:
1. Download Mathlib's declaration cache (fast, no build needed)
2. Search Lean Reservoir and GitHub for repos matching your `search_keywords`
3. Clone and parse each discovered repo's `.lean` files
4. Match declarations to your topics
5. Store everything in `data/index.db`

First run takes a few minutes. Subsequent runs are incremental (only re-indexes repos with new commits).

## Step 6: Verify

```bash
# Check what was indexed
lean-index stats

# Search your topics
lean-index search "scheme" --topic algebraic-geometry
lean-index search --kind theorem --since 2026-01-01

# List tracked repos
lean-index repos
```

## Step 7: Set up the repo for publishing

```bash
# .gitignore
cat > .gitignore << 'EOF'
data/
__pycache__/
*.egg-info/
.env
EOF

# Initial commit
git add topics.yaml repos.yaml .gitignore
git commit -m "Initial topic configuration"

# Create GitHub repo and push
gh repo create lean-index-mytopic --public --source=. --push \
  --description "Lean 4 declaration index for algebraic geometry"
```

## Step 8: Set up automated builds

Create `.github/workflows/update.yml` to rebuild and publish the database weekly:

```yaml
name: Update Index
on:
  schedule:
    - cron: '0 6 * * 1'  # Weekly Monday 6 AM UTC
  workflow_dispatch:       # Manual trigger

jobs:
  update:
    runs-on: ubuntu-latest
    permissions:
      contents: write      # Needed for creating releases
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install lean-index
        run: pip install git+https://github.com/mrdouglasny/lean-index.git

      - name: Build index
        run: |
          lean-index init
          lean-index update

      - name: Generate reports
        run: |
          lean-index stats > STATS.md
          lean-index changelog --since "$(date -d '7 days ago' +%Y-%m-%d)" > CHANGELOG.md

      - name: Publish database as GitHub release
        run: |
          gh release create "$(date +%Y-%m-%d)" data/index.db \
            --title "Index $(date +%Y-%m-%d)" \
            --notes "$(cat CHANGELOG.md)"
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Commit reports
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add STATS.md CHANGELOG.md
          git diff --staged --quiet || git commit -m "Update reports $(date +%Y-%m-%d)"
          git push
```

```bash
git add .github/workflows/update.yml
git commit -m "Add weekly index update workflow"
git push
```

You can trigger the first build manually from the GitHub Actions tab, or wait for the weekly schedule.

## Step 9: Tell people about it

Once the first release is published, anyone can use your topic index:

```bash
pip install git+https://github.com/mrdouglasny/lean-index.git
lean-index fetch-db yourname/lean-index-mytopic
lean-index search "scheme"
```

## Maintaining your topic repo

- **Add new curated repos**: edit `repos.yaml`, push, wait for next CI run (or trigger manually)
- **Refine topic matchers**: edit `topics.yaml` — add module prefixes as Mathlib grows, add type mentions for new concepts
- **Force full rebuild**: trigger the workflow manually from GitHub Actions
- **Check what changed**: look at the auto-generated `CHANGELOG.md` after each CI run

## Directory structure summary

```
lean-index-mytopic/
  topics.yaml                # Topic definitions (matchers + search keywords)
  repos.yaml                 # Curated repo list (optional)
  STATS.md                   # Auto-generated by CI
  CHANGELOG.md               # Auto-generated by CI
  .github/workflows/
    update.yml               # Weekly build + publish
  .gitignore                 # Excludes data/
  data/                      # .gitignored, built by lean-index
    index.db                 # SQLite database (published as release)
    mathlib-cache/           # Cached Mathlib declarations
```
