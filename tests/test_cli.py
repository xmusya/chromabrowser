from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str, timeout: float = 30.0, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(
        TERM="dumb",
        PYTHONIOENCODING="utf-8",
        XDG_CONFIG_HOME="/tmp/chroma-pytest-cli/config",
        XDG_DATA_HOME="/tmp/chroma-pytest-cli/data",
        XDG_CACHE_HOME="/tmp/chroma-pytest-cli/cache",
    )
    env.pop("COLUMNS", None)
    env.pop("LINES", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "chromabrowser", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture
def demo(tmp_path):
    page = tmp_path / "demo.html"
    page.write_text(
        "<html><head><title>Demo</title></head><body>"
        "<h1>Big heading</h1><p>Paragraph with a <a href='x.html'>link</a>.</p>"
        "<ul><li>alpha</li><li>beta</li></ul></body></html>",
        encoding="utf-8",
    )
    return page


def test_version():
    result = run_cli("--version")
    assert result.returncode == 0
    assert "ChromaBrowser" in result.stdout


def test_help():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


def test_dump_file(demo):
    result = run_cli("--dump", "--width", "40", "--color", "never", demo.as_uri())
    assert result.returncode == 0, result.stderr
    assert "Big heading" in result.stdout
    assert "• alpha" in result.stdout
    assert "• beta" in result.stdout
    assert "Paragraph with a link." in result.stdout
    assert "\x1b[" not in result.stdout


def test_dump_accepts_plain_local_path(demo):
    result = run_cli("--dump", "--width", "40", "--color", "never", str(demo))
    assert result.returncode == 0, result.stderr
    assert "Big heading" in result.stdout


def test_dump_wraps_to_width(demo):
    result = run_cli("--dump", "--width", "20", "--color", "never", demo.as_uri())
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert all(len(line) <= 20 for line in lines)


def test_dump_no_images_flag(tmp_path):
    page = tmp_path / "img.html"
    page.write_text("<img src='x.png' alt='pic'>", encoding="utf-8")
    result = run_cli("--dump", "--width", "40", "--no-images", page.as_uri())
    assert "[image: pic]" in result.stdout


def test_dump_about_start():
    result = run_cli("--dump", "--width", "90", "about:start")
    assert result.returncode == 0
    assert "Quick start" in result.stdout
    assert "a colorful browser" in result.stdout


def test_dump_missing_file():
    result = run_cli("--dump", "file:///no/such/file.html")
    assert result.returncode == 2
    assert "chroma:" in result.stderr


def test_dump_unknown_scheme():
    result = run_cli("--dump", "gopher://example.com/")
    assert result.returncode == 2
    assert "chroma:" in result.stderr


def test_dump_ansi_output_when_forced(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<h1>Color</h1>", encoding="utf-8")
    result = run_cli("--dump", "--width", "40", "--color", "truecolor", page.as_uri())
    assert result.returncode == 0
    assert "Color" in result.stdout
    assert "\x1b[" in result.stdout
    assert "38;2;" in result.stdout


def test_dump_256_color_output(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<h1>Color</h1>", encoding="utf-8")
    result = run_cli("--dump", "--width", "40", "--color", "256", page.as_uri())
    assert result.returncode == 0
    assert "38;5;" in result.stdout
    assert "38;2;" not in result.stdout


def test_dump_never_color_output(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<h1>Color</h1>", encoding="utf-8")
    result = run_cli("--dump", "--width", "40", "--color", "never", page.as_uri())
    assert "\x1b[" not in result.stdout


def test_interactive_requires_tty():
    result = run_cli("about:start")
    assert result.returncode == 1
    assert "interactive terminal" in result.stderr
