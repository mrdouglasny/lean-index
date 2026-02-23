# Copyright 2026 Michael R. Douglas. MIT License.
"""Configuration loading for topics and repos."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class TopicMatcher:
    module_prefixes: list[str] = field(default_factory=list)
    type_mentions: list[str] = field(default_factory=list)
    name_patterns: list[str] = field(default_factory=list)


@dataclass
class TopicConfig:
    name: str
    search_keywords: list[str] = field(default_factory=list)
    matchers: TopicMatcher = field(default_factory=TopicMatcher)

    @classmethod
    def from_dict(cls, d: dict) -> "TopicConfig":
        matchers_raw = d.get("matchers", {})
        matchers = TopicMatcher(
            module_prefixes=matchers_raw.get("module_prefixes", []),
            type_mentions=matchers_raw.get("type_mentions", []),
            name_patterns=matchers_raw.get("name_patterns", []),
        )
        return cls(
            name=d["name"],
            search_keywords=d.get("search_keywords", []),
            matchers=matchers,
        )


@dataclass
class RepoEntry:
    url: str
    description: str = ""
    branch: str = "main"


@dataclass
class LocalRepoEntry:
    """A repo in local-repos.yaml. Has either path (local) or url (remote)."""
    path: str = ""
    url: str = ""
    name: str = ""
    description: str = ""
    branch: str = "main"


@dataclass
class IndexConfig:
    topics: list[TopicConfig] = field(default_factory=list)
    repos: list[RepoEntry] = field(default_factory=list)
    local_repos: list[LocalRepoEntry] = field(default_factory=list)
    blocked_repos: set[str] = field(default_factory=set)
    data_dir: Path = field(default_factory=lambda: Path("data"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "index.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "mathlib-cache"


def load_blocklist(config_dir: Path | None = None) -> set[str]:
    """Load blocked repo URLs from engine + local blocklist.yaml files.

    Merges the built-in blocklist (shipped with lean-index) with any
    local blocklist.yaml found in the config directory.
    """
    blocked = set()

    # 1. Built-in blocklist (in the leanindex package)
    builtin = Path(__file__).parent / "blocklist.yaml"
    for path in [builtin, config_dir / "blocklist.yaml" if config_dir else None]:
        if path and path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            for entry in data.get("blocked_repos", []):
                url = entry if isinstance(entry, str) else entry.get("url", "")
                if url:
                    blocked.add(url.rstrip("/"))

    return blocked


def find_config_dir(start: Path | None = None) -> Path:
    """Find config directory: check CWD, then index/ subdirectory."""
    start = start or Path.cwd()

    # Check for topics.yaml in CWD
    if (start / "topics.yaml").exists():
        return start

    # Check index/ subdirectory
    if (start / "index" / "topics.yaml").exists():
        return start / "index"

    # Default to CWD
    return start


def load_config(config_dir: Path | None = None,
                data_dir: Path | None = None) -> IndexConfig:
    """Load configuration from topics.yaml and repos.yaml."""
    config_dir = config_dir or find_config_dir()
    config_dir = Path(config_dir)

    topics = []
    topics_file = config_dir / "topics.yaml"
    if topics_file.exists():
        with open(topics_file) as f:
            data = yaml.safe_load(f) or {}
        for t in data.get("topics", []):
            topics.append(TopicConfig.from_dict(t))

    repos = []
    repos_file = config_dir / "repos.yaml"
    if repos_file.exists():
        with open(repos_file) as f:
            data = yaml.safe_load(f) or {}
        for r in data.get("repos", []):
            repos.append(RepoEntry(
                url=r["url"],
                description=r.get("description", ""),
                branch=r.get("branch", "main"),
            ))

    local_repos = []
    local_repos_file = config_dir / "local-repos.yaml"
    if local_repos_file.exists():
        with open(local_repos_file) as f:
            data = yaml.safe_load(f) or {}
        for r in data.get("repos", []):
            local_repos.append(LocalRepoEntry(
                path=str(Path(r["path"]).expanduser()) if r.get("path") else "",
                url=r.get("url", ""),
                name=r.get("name", ""),
                description=r.get("description", ""),
                branch=r.get("branch", "main"),
            ))

    resolved_data_dir = Path(data_dir) if data_dir else config_dir / "data"
    blocked = load_blocklist(config_dir)

    return IndexConfig(
        topics=topics,
        repos=repos,
        local_repos=local_repos,
        blocked_repos=blocked,
        data_dir=resolved_data_dir,
    )
