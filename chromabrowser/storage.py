"""On-disk state: config, cookies, history, bookmarks and caches."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    base = Path(value) if value else Path.home() / default
    return base.expanduser()


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "chromabrowser"
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "chromabrowser"
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "chromabrowser"


def ensure_dirs() -> None:
    for path in (CONFIG_DIR, DATA_DIR, CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "images").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "downloads").mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        tmp.replace(path)
    except OSError:
        pass


@dataclass
class Config:
    homepage: str = "about:start"
    search_engine: str = "https://lite.duckduckgo.com/lite/?q={query}"
    images: bool = True
    zoom: float = 1.0
    max_image_width: int = 96
    color: str = "auto"  # auto | truecolor | 256
    download_dir: str = "~/Downloads"
    page_timeout: float = 25.0
    image_timeout: float = 8.0
    max_page_mb: float = 25.0
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) ChromaBrowser/1.0"
    )

    @classmethod
    def load(cls) -> "Config":
        ensure_dirs()
        raw = load_json(CONFIG_DIR / "config.json", {})
        if not isinstance(raw, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known}
        try:
            return cls(**data)
        except (TypeError, ValueError):
            return cls()

    def save(self) -> None:
        save_json(CONFIG_DIR / "config.json", asdict(self))


@dataclass
class HistoryEntry:
    url: str
    title: str = ""
    ts: float = 0.0


class History:
    def __init__(self, path: Path | None = None, limit: int = 1000):
        self.path = path or (DATA_DIR / "history.json")
        self.limit = limit
        raw = load_json(self.path, [])
        self.entries: list[HistoryEntry] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("url"):
                    self.entries.append(
                        HistoryEntry(
                            url=str(item["url"]),
                            title=str(item.get("title", "")),
                            ts=float(item.get("ts", 0) or 0),
                        )
                    )

    def add(self, url: str, title: str = "") -> None:
        if url.startswith("about:"):
            return
        self.entries = [e for e in self.entries if e.url != url]
        self.entries.append(HistoryEntry(url=url, title=title, ts=time.time()))
        if len(self.entries) > self.limit:
            self.entries = self.entries[-self.limit :]
        self.save()

    def recent(self, limit: int = 100) -> list[HistoryEntry]:
        return list(reversed(self.entries))[:limit]

    def remove(self, url: str) -> None:
        self.entries = [e for e in self.entries if e.url != url]
        self.save()

    def clear(self) -> None:
        self.entries = []
        self.save()

    def save(self) -> None:
        save_json(
            self.path,
            [{"url": e.url, "title": e.title, "ts": e.ts} for e in self.entries],
        )


class Bookmarks:
    def __init__(self, path: Path | None = None):
        self.path = path or (DATA_DIR / "bookmarks.json")
        raw = load_json(self.path, [])
        self.items: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("url"):
                    self.items.append(
                        {
                            "url": str(item["url"]),
                            "title": str(item.get("title", "")),
                            "ts": float(item.get("ts", 0) or 0),
                        }
                    )

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def contains(self, url: str) -> bool:
        return any(item["url"] == url for item in self.items)

    def add(self, url: str, title: str = "") -> None:
        if self.contains(url):
            return
        self.items.append({"url": url, "title": title, "ts": time.time()})
        self.save()

    def remove(self, url: str) -> None:
        self.items = [item for item in self.items if item["url"] != url]
        self.save()

    def toggle(self, url: str, title: str = "") -> bool:
        if self.contains(url):
            self.remove(url)
            return False
        self.add(url, title)
        return True

    def save(self) -> None:
        save_json(self.path, self.items)
