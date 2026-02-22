"""Discover Lean 4 repos from Lean Reservoir and GitHub search."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

import requests

from .config import IndexConfig, TopicConfig

logger = logging.getLogger(__name__)

RESERVOIR_INDEX_REPO = "leanprover/reservoir-index"


def discover_reservoir(cache_dir: Path, token: str | None = None) -> list[dict]:
    """Enumerate packages from the Lean Reservoir index.

    Clones the reservoir-index repo (shallow) and reads metadata.json files.
    Structure: owner/package-name/metadata.json
    Caches the result locally.
    """
    cache_file = cache_dir / "reservoir-packages.json"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Use cache if fresh (< 24 hours)
    if cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < 24:
            logger.info(f"Using cached Reservoir data ({age_hours:.1f}h old)")
            with open(cache_file) as f:
                return json.load(f)

    logger.info("Cloning Lean Reservoir index...")
    import tempfile
    packages = []

    with tempfile.TemporaryDirectory(prefix="lean-index-reservoir-") as tmpdir:
        clone_dir = Path(tmpdir) / "reservoir-index"
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1",
                 f"https://github.com/{RESERVOIR_INDEX_REPO}.git",
                 str(clone_dir)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                logger.warning(f"Failed to clone Reservoir index: {result.stderr}")
                if cache_file.exists():
                    with open(cache_file) as f:
                        return json.load(f)
                return []
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"Could not clone Reservoir index: {e}")
            if cache_file.exists():
                with open(cache_file) as f:
                    return json.load(f)
            return []

        # Walk the cloned repo: owner/package/metadata.json
        for metadata_file in clone_dir.rglob("metadata.json"):
            try:
                with open(metadata_file) as f:
                    meta = json.load(f)

                # Extract repo URL from sources
                url = ""
                branch = "main"
                sources = meta.get("sources", [])
                if sources:
                    url = sources[0].get("repoUrl", sources[0].get("gitUrl", ""))
                    branch = sources[0].get("defaultBranch", "main")

                if not url:
                    continue

                packages.append({
                    "name": meta.get("name", ""),
                    "url": url,
                    "description": meta.get("description", ""),
                    "stars": meta.get("stars", 0),
                    "branch": branch,
                    "source": "reservoir",
                })
            except (json.JSONDecodeError, KeyError) as e:
                logger.debug(f"Could not parse {metadata_file}: {e}")

    logger.info(f"Found {len(packages)} Reservoir packages")

    # Cache results
    with open(cache_file, "w") as f:
        json.dump(packages, f)

    return packages


def discover_github(topics: list[TopicConfig], token: str | None = None) -> list[dict]:
    """Search GitHub for Lean repos matching topic keywords.

    Uses `gh search repos` CLI if available, falls back to API.
    """
    repos = []
    seen_urls = set()

    for topic in topics:
        for keyword in topic.search_keywords[:3]:  # Limit queries per topic
            query = f"{keyword} language:Lean"
            try:
                result = subprocess.run(
                    ["gh", "search", "repos", query,
                     "--limit", "20", "--json", "url,name,description,stargazersCount"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    items = json.loads(result.stdout)
                    for item in items:
                        url = item.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            repos.append({
                                "url": url,
                                "name": item.get("name", ""),
                                "description": item.get("description", ""),
                                "stars": item.get("stargazersCount", 0),
                                "source": "github_search",
                                "search_topic": topic.name,
                            })
                time.sleep(2)  # Rate limit
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                logger.debug(f"gh search failed for '{keyword}': {e}")
                # Fallback: use the API directly
                _github_api_search(keyword, repos, seen_urls, topic.name, token)

    logger.info(f"Discovered {len(repos)} repos via GitHub search")
    return repos


def _github_api_search(keyword: str, repos: list, seen_urls: set,
                       topic_name: str, token: str | None):
    """Fallback: search GitHub API for repos."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    query = f"{keyword} language:Lean"
    try:
        resp = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "per_page": 20, "sort": "stars"},
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 200:
            for item in resp.json().get("items", []):
                url = item.get("html_url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    repos.append({
                        "url": url,
                        "name": item.get("name", ""),
                        "description": item.get("description", ""),
                        "stars": item.get("stargazers_count", 0),
                        "source": "github_search",
                        "search_topic": topic_name,
                    })
        time.sleep(3)
    except Exception as e:
        logger.debug(f"GitHub API search failed for '{keyword}': {e}")


def _repo_matches_topics(repo: dict, topics: list) -> bool:
    """Quick check if a repo name/description suggests topic relevance.

    This is a cheap pre-filter to avoid cloning hundreds of irrelevant repos.
    We check repo name and description against topic search_keywords.
    Mathlib and curated repos always pass.
    """
    source = repo.get("source", "")
    if source == "curated":
        return True

    name = (repo.get("name", "") or "").lower()
    desc = (repo.get("description", "") or "").lower()
    text = f"{name} {desc}"

    # Check against topic keywords
    for topic in topics:
        for kw in topic.search_keywords:
            if kw.lower() in text:
                return True
        # Also check type mentions as they often appear in repo names
        for mention in topic.matchers.type_mentions:
            if mention.lower() in text:
                return True

    return False


def discover_all(config: IndexConfig, cache_dir: Path) -> list[dict]:
    """Run all discovery strategies and return combined repo list."""
    all_repos = []
    seen_urls = set()

    # 1. Curated repos from config
    for repo in config.repos:
        if repo.url not in seen_urls:
            seen_urls.add(repo.url)
            all_repos.append({
                "url": repo.url,
                "name": repo.url.rstrip("/").split("/")[-1],
                "description": repo.description,
                "source": "curated",
                "branch": repo.branch,
            })

    # 2. Reservoir packages
    reservoir = discover_reservoir(cache_dir)
    for pkg in reservoir:
        url = pkg.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            all_repos.append({
                "url": url,
                "name": pkg.get("name", ""),
                "description": pkg.get("description", ""),
                "stars": pkg.get("stars", 0),
                "source": "reservoir",
            })

    # 3. GitHub search
    if config.topics:
        gh_repos = discover_github(config.topics)
        for repo in gh_repos:
            if repo["url"] not in seen_urls:
                seen_urls.add(repo["url"])
                all_repos.append(repo)

    logger.info(f"Total discovered repos: {len(all_repos)}")
    return all_repos
