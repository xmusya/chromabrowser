from __future__ import annotations

import json

from chromabrowser import storage
from chromabrowser.storage import Bookmarks, Config, History


def test_history_add_dedupe_and_order(tmp_path):
    h = History(tmp_path / "history.json")
    h.add("https://a.test/", "A")
    h.add("https://b.test/", "B")
    h.add("https://a.test/", "A2")
    urls = [e.url for e in h.recent()]
    assert urls == ["https://a.test/", "https://b.test/"]
    assert h.recent()[0].title == "A2"
    # Persisted
    again = History(tmp_path / "history.json")
    assert [e.url for e in again.recent()] == urls


def test_history_skips_about_and_can_remove_clear(tmp_path):
    h = History(tmp_path / "history.json")
    h.add("about:start")
    assert h.recent() == []
    h.add("https://x.test/")
    h.remove("https://x.test/")
    assert h.recent() == []
    h.add("https://y.test/")
    h.clear()
    assert h.recent() == []


def test_history_limit(tmp_path):
    h = History(tmp_path / "history.json", limit=2)
    for i in range(5):
        h.add(f"https://e.test/{i}")
    assert len(h.recent()) == 2


def test_bookmarks_toggle_and_persist(tmp_path):
    b = Bookmarks(tmp_path / "b.json")
    assert not b.contains("https://a.test/")
    assert b.toggle("https://a.test/", "A") is True
    assert b.contains("https://a.test/")
    assert b.toggle("https://a.test/") is False
    assert len(b) == 0
    b.add("https://a.test/", "A")
    again = Bookmarks(tmp_path / "b.json")
    assert len(again) == 1
    again.add("https://a.test/", "dupe")
    assert len(again) == 1
    again.remove("https://a.test/")
    assert len(again) == 0


def test_bookmarks_survive_corrupt_files(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert len(Bookmarks(path)) == 0
    assert History(path).recent() == []


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(storage, "CACHE_DIR", tmp_path / "cache")
    cfg = Config(homepage="https://example.test", zoom=1.5, images=False)
    cfg.save()
    loaded = Config.load()
    assert loaded.homepage == "https://example.test"
    assert loaded.zoom == 1.5
    assert loaded.images is False
    raw = json.loads((tmp_path / "cfg" / "config.json").read_text(encoding="utf-8"))
    assert raw["homepage"] == "https://example.test"


def test_config_ignores_unknown_and_bad_values(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(storage, "CACHE_DIR", tmp_path / "cache")
    (tmp_path / "cfg").mkdir(parents=True)
    (tmp_path / "cfg" / "config.json").write_text(
        '{"homepage": "https://x.test", "nonsense": 1}', encoding="utf-8"
    )
    cfg = Config.load()
    assert cfg.homepage == "https://x.test"
    assert not hasattr(cfg, "nonsense")
