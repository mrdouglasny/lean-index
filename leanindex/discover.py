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
RESERVOIR_API = f"https://api.github.com/repos/{RESERVOIR_INDEX_REPO}/contents/packages"


def discover_reservoir(cache_dir: Path, token: str | None = None) -> list[dict]:
    """Enumerate packages from the Lean Reservoir index.

    Uses the GitHub Contents API to list packages in the reservoir-index repo.
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

    logger.info("Fetching Lean Reservoir package list...")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    packages = []
    page = 1
    per_page = 100

    while True:
        resp = requests.get(
            RESERVOIR_API,
            params={"per_page": per_page, "page": page},
            headers=headers,
            timeout=30,
        )

        if resp.status_code == 403:
            logger.warning("GitHub API rate limited. Using cached data if available.")
            if cache_file.exists():
                with open(cache_file) as f:
                    return json.load(f)
            return packages

        resp.raise_for_status()
        items = resp.json()

        if not items:
            break

        for item in items:
            if item.get("type") == "dir":
                packages.append({
                    "name": item["name"],
                    "reservoir_path": item["path"],
                })

        if len(items) < per_page:
            break
        page += 1
        time.sleep(0.5)  # Rate limiting

    logger.info(f"Found {len(packages)} Reservoir packages")

    # Fetch metadata for each package (repo URL, description, stars)
    enriched = []
    for i, pkg in enumerate(packages):
        if i % 50 == 0 and i > 0:
            logger.info(f"  Fetching metadata: {i}/{len(packages)}")
            time.sleep(1)

        meta_url = f"https://api.github.com/repos/{RESERVOIR_INDEX_REPO}/contents/{pkg['reservoir_path']}/metadata.json"
        try:
            resp = requests.get(meta_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                import base64
                content = base64.b64decode(resp.json()["content"])
                meta = json.loads(content)
                pkg.update({
                    "url": meta.get("url", meta.get("source", {}).get("url", "")),
                    "description": meta.get("description", ""),
                    "stars": meta.get("stars", 0),
                })
            time.sleep(0.3)
        except Exception as e:
            logger.debug(f"  Could not fetch metadata for {pkg['name']}: {e}")

        enriched.append(pkg)

    # Cache results
    with open(cache_file, "w") as f:
        json.dump(enriched, f)

    return enriched


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
