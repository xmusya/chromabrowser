"""Networking: page fetching, URL resolution, images and downloads."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, unquote_to_bytes, urlparse

import requests

from . import storage
from .storage import CACHE_DIR, Config

CHROME_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,image/*;q=0.8,*/*;q=0.7"
)
_SCHEMES = ("http://", "https://", "file://", "about:", "data:")
_DOMAIN_RE = re.compile(r"^[\w.-]+\.[a-zA-Z]{2,}(?::\d+)?(?:[/?#].*)?$")
_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE
)


class FetchError(Exception):
    def __init__(self, message: str, *, url: str = "", status: int | None = None):
        super().__init__(message)
        self.message = message
        self.url = url
        self.status = status


@dataclass
class Fetched:
    url: str
    status: int
    headers: dict[str, str]
    content: bytes
    text: str
    content_type: str
    charset: str = "utf-8"
    elapsed: float = 0.0

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type or "xml" in self.content_type

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/") and "svg" not in self.content_type


@dataclass
class Download:
    filename: str
    content: bytes
    content_type: str = ""


def _guess_charset(content: bytes, headers: dict[str, str]) -> str:
    ctype = headers.get("content-type", "")
    m = re.search(r"charset\s*=\s*[\"']?([\w-]+)", ctype, re.I)
    if m:
        return m.group(1)
    m2 = _META_CHARSET_RE.search(content[:8192])
    if m2:
        try:
            return m2.group(1).decode("ascii", "ignore")
        except Exception:
            pass
    return "utf-8"


def decode_content(content: bytes, headers: dict[str, str]) -> tuple[str, str]:
    charset = _guess_charset(content, headers)
    for enc in (charset, "utf-8", "cp1251", "latin-1"):
        try:
            return content.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", "replace"), "utf-8"


def humanize_net_error(exc: BaseException) -> str:
    if isinstance(exc, requests.exceptions.SSLError):
        return "TLS/SSL error: the certificate could not be verified"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connection timed out"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "the server took too long to respond"
    if isinstance(exc, requests.exceptions.TooManyRedirects):
        return "too many redirects"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "could not connect to the server"
    if isinstance(exc, requests.exceptions.InvalidURL):
        return "invalid URL"
    return str(exc) or exc.__class__.__name__


class BrowserSession:
    """A requests session plus ChromaBrowser's caches."""

    def __init__(self, config: Config):
        self.config = config
        storage.ensure_dirs()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": CHROME_ACCEPT,
                "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            }
        )
        self.cookies_path = storage.DATA_DIR / "cookies.txt"
        self._load_cookies()
        self._image_mem: dict[str, bytes | None] = {}
        self.last_error: str | None = None

    # ------------------------------------------------------------- cookies ---
    def _load_cookies(self) -> None:
        if not self.cookies_path.exists():
            return
        try:
            from http.cookiejar import MozillaCookieJar

            jar = MozillaCookieJar(str(self.cookies_path))
            jar.load(ignore_discard=True, ignore_expires=True)
            self.session.cookies.update(jar)
        except Exception:
            pass

    def save_cookies(self) -> None:
        try:
            from http.cookiejar import MozillaCookieJar

            jar = MozillaCookieJar(str(self.cookies_path))
            for cookie in self.session.cookies:
                jar.set_cookie(cookie)
            jar.save(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass

    def close(self) -> None:
        self.save_cookies()
        try:
            self.session.close()
        except Exception:
            pass

    # ----------------------------------------------------------------- url ---
    def resolve(self, target: str) -> str:
        target = (target or "").strip()
        if not target:
            return self.config.homepage
        low = target.lower()
        if low.startswith("about:"):
            return low
        if low.startswith(_SCHEMES):
            return target
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target):
            # Unknown but explicit scheme: let the fetcher report it properly.
            return target
        if target.startswith(("/", "./", "../", "~")):
            return Path(target).expanduser().resolve().as_uri()
        if os.path.exists(os.path.expanduser(target)) and " " not in target:
            return Path(target).expanduser().resolve().as_uri()
        if " " not in target and (_DOMAIN_RE.match(target) or low.startswith("localhost")):
            return "https://" + target
        template = self.config.search_engine
        if "{query}" in template:
            return template.replace("{query}", quote(target))
        return template + quote(target)

    def is_url(self, target: str) -> bool:
        return bool(target.strip())

    # --------------------------------------------------------------- pages ---
    def fetch(self, url: str, *, referer: str | None = None, timeout: float | None = None) -> Fetched:
        started = time.monotonic()
        parsed = urlparse(url)
        if parsed.scheme == "file":
            return self._fetch_file(url, started)
        if parsed.scheme not in ("http", "https"):
            raise FetchError(f"Unsupported scheme: {parsed.scheme or '(none)'}", url=url)

        headers = {"Referer": referer} if referer else {}
        timeout = timeout or self.config.page_timeout
        limit = int(self.config.max_page_mb * 1024 * 1024)
        try:
            resp = self.session.get(
                url,
                timeout=(10, timeout),
                stream=True,
                allow_redirects=True,
                headers=headers,
            )
        except requests.RequestException as exc:
            raise FetchError(humanize_net_error(exc), url=url) from exc

        try:
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    raise FetchError(
                        f"page is larger than {self.config.max_page_mb:.0f} MB", url=resp.url
                    )
                chunks.append(chunk)
            content = b"".join(chunks)
        except requests.RequestException as exc:
            raise FetchError(humanize_net_error(exc), url=url) from exc
        finally:
            resp.close()

        if resp.status_code >= 400:
            # Still return the body (servers send useful error pages), but flag it.
            self.last_error = f"HTTP {resp.status_code} {resp.reason}"
        else:
            self.last_error = None
        headers_dict = {k.lower(): v for k, v in resp.headers.items()}
        content_type = headers_dict.get("content-type", "").split(";")[0].strip().lower()
        if not content_type:
            content_type = mimetypes.guess_type(resp.url)[0] or "text/html"
        text, charset = decode_content(content, headers_dict)
        return Fetched(
            url=resp.url,
            status=resp.status_code,
            headers=headers_dict,
            content=content,
            text=text,
            content_type=content_type,
            charset=charset,
            elapsed=time.monotonic() - started,
        )

    def _fetch_file(self, url: str, started: float) -> Fetched:
        parsed = urlparse(url)
        path = Path(unquote(parsed.path))
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise FetchError(f"file error: {exc.strerror or exc}", url=url) from exc
        ctype = mimetypes.guess_type(str(path))[0] or "text/plain"
        headers = {"content-type": ctype}
        text, charset = decode_content(data, headers)
        return Fetched(
            url=path.as_uri(),
            status=200,
            headers=headers,
            content=data,
            text=text,
            content_type=ctype.split(";")[0],
            charset=charset,
            elapsed=time.monotonic() - started,
        )

    # -------------------------------------------------------------- images ---
    def fetch_image(self, url: str, *, referer: str | None = None, timeout: float | None = None) -> bytes:
        if url in self._image_mem:
            cached = self._image_mem[url]
            if cached is None:
                raise FetchError("image previously failed", url=url)
            return cached

        if url.startswith("data:"):
            header, _, payload = url.partition(",")
            try:
                if ";base64" in header.lower():
                    data = base64.b64decode(payload, validate=False)
                else:
                    data = unquote_to_bytes(payload)
            except Exception as exc:
                self._image_mem[url] = None
                raise FetchError("invalid data: URL", url=url) from exc
            if not data:
                self._image_mem[url] = None
                raise FetchError("empty image", url=url)
            self._image_mem[url] = data
            return data

        if url.startswith("file://"):
            try:
                data = Path(unquote(urlparse(url).path)).read_bytes()
            except OSError as exc:
                self._image_mem[url] = None
                raise FetchError(str(exc), url=url) from exc
            self._image_mem[url] = data
            return data

        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        cache_dir = CACHE_DIR / "images"
        cache_dir.mkdir(parents=True, exist_ok=True)
        existing = list(cache_dir.glob(key + ".*"))
        if existing:
            try:
                data = existing[0].read_bytes()
                self._image_mem[url] = data
                return data
            except OSError:
                pass

        headers = {"Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
        if referer:
            headers["Referer"] = referer
        timeout = timeout or self.config.image_timeout
        try:
            resp = self.session.get(url, headers=headers, timeout=(6, timeout), stream=True)
            resp.raise_for_status()
            buf = bytearray()
            for chunk in resp.iter_content(32 * 1024):
                buf.extend(chunk)
                if len(buf) > 15 * 1024 * 1024:
                    raise FetchError("image is too large", url=url)
            data = bytes(buf)
            resp.close()
        except requests.RequestException as exc:
            self._image_mem[url] = None
            raise FetchError(humanize_net_error(exc), url=url) from exc
        if not data:
            self._image_mem[url] = None
            raise FetchError("empty image", url=url)

        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
        ext = mimetypes.guess_extension(ctype) or Path(urlparse(url).path).suffix or ".img"
        if ext in (".jpe", ".jpeg"):
            ext = ".jpg"
        try:
            (cache_dir / (key + ext)).write_bytes(data)
        except OSError:
            pass
        self._image_mem[url] = data
        return data

    # ----------------------------------------------------------- downloads ---
    def download(self, url: str, *, referer: str | None = None) -> Download:
        if url.startswith("file://"):
            path = Path(unquote(urlparse(url).path))
            return Download(path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "")
        headers = {"Referer": referer} if referer else {}
        try:
            resp = self.session.get(url, stream=True, timeout=(10, 60), headers=headers)
            resp.raise_for_status()
            chunks = []
            total = 0
            for chunk in resp.iter_content(128 * 1024):
                total += len(chunk)
                if total > 200 * 1024 * 1024:
                    raise FetchError("file is larger than 200 MB", url=url)
                chunks.append(chunk)
            content = b"".join(chunks)
        except requests.RequestException as exc:
            raise FetchError(humanize_net_error(exc), url=url) from exc

        filename = ""
        disp = resp.headers.get("content-disposition", "")
        m = re.search(r"filename\*=UTF-8''([^;]+)", disp, re.I)
        if m:
            filename = unquote(m.group(1))
        else:
            m = re.search(r'filename="?([^";]+)"?', disp, re.I)
            if m:
                filename = m.group(1)
        if not filename:
            filename = os.path.basename(unquote(urlparse(resp.url).path)) or "download"
        filename = re.sub(r"[/\\]", "_", filename).strip() or "download"
        return Download(filename, content, resp.headers.get("content-type", ""))

    # ------------------------------------------------------------- helpers ---
    def site_label(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            return "local file"
        if parsed.scheme == "about":
            return "internal page"
        return parsed.hostname or parsed.netloc or url
