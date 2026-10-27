#!/usr/bin/env python3
"""
HTTP kecil dengan rate limit per host, retry 429/5xx, dan state yang
disimpan ke disk supaya proses cron berikutnya tidak mengulang kesalahan.

Tidak ada dependensi luar; hanya urllib.
"""

import contextlib
import fcntl
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config


class SourceError(RuntimeError):
    pass


class HostLimiter:
    """Jeda minimum antar panggilan ke satu host, adaptif saat kena 429."""

    def __init__(self, name, min_interval):
        self.name = name
        self.base = float(min_interval)
        self.interval = float(min_interval)
        self.last = 0.0
        self.state_path = config.DATA_DIR / ("rate_%s.json" % name)
        self.lock_path = config.DATA_DIR / ("rate_%s.lock" % name)
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.interval = max(self.base, float(raw.get("interval", self.base)))
            self.last = float(raw.get("last", 0.0))
        except (OSError, ValueError, TypeError):
            pass

    def _save(self):
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"interval": round(self.interval, 3), "last": self.last}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def wait(self):
        gap = self.interval - (time.time() - self.last)
        if gap > 0:
            time.sleep(min(gap, 60.0))
        self.last = time.time()
        self._save()

    @contextlib.contextmanager
    def guard(self):
        """
        Kunci antar-proses untuk satu host.

        Scanner dan tracker berjalan sebagai proses cron terpisah; tanpa kunci
        ini keduanya bisa menembak host yang sama bersamaan dan memicu 429.
        """
        handle = None
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            handle = open(self.lock_path, "a+")
            fcntl.flock(handle, fcntl.LOCK_EX)
        except OSError:
            handle = None
        try:
            # Baca ulang state: proses lain mungkin baru saja memperbaruinya.
            self._load()
            self.wait()
            yield
        finally:
            if handle is not None:
                try:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                finally:
                    handle.close()

    def penalize(self, retry_after=None):
        """429: naikkan jeda, jangan turunkan sebelum beberapa sukses."""
        if retry_after and retry_after > 0:
            self.interval = max(self.interval * 1.5, min(retry_after, 60.0))
        else:
            self.interval = min(max(self.interval * 2, 10.0), 60.0)
        self._save()

    def reward(self):
        """Sukses: turunkan perlahan, tidak langsung kembali ke basis."""
        if self.interval > self.base:
            self.interval = max(self.base, self.interval * 0.92)
            self._save()


_LIMITERS = {}


def limiter(name, min_interval):
    if name not in _LIMITERS:
        _LIMITERS[name] = HostLimiter(name, min_interval)
    return _LIMITERS[name]


def get_json(url, lim=None, timeout=None, retries=None):
    timeout = config.HTTP_TIMEOUT if timeout is None else timeout
    retries = config.HTTP_RETRIES if retries is None else retries
    headers = {
        "Accept": "application/json",
        "User-Agent": config.USER_AGENT,
    }
    last_error = None
    for attempt in range(retries + 1):
        fatal = False
        guard = lim.guard() if lim is not None else contextlib.nullcontext()
        try:
            with guard:
                req = urllib.request.Request(url, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        body = resp.read()
                        if lim is not None:
                            lim.reward()
                        return json.loads(body.decode("utf-8", "replace"))
                except urllib.error.HTTPError as exc:
                    last_error = exc
                    if lim is not None and exc.code == 429:
                        retry_after = None
                        try:
                            retry_after = float(exc.headers.get("Retry-After", ""))
                        except (TypeError, ValueError):
                            pass
                        lim.penalize(retry_after)
                    fatal = exc.code in (400, 401, 403, 404)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if fatal:
            raise SourceError("%s: HTTP %s" % (url, last_error.code)) from last_error
        if attempt >= retries:
            break
        time.sleep(min(2 ** attempt, 10))
    raise SourceError("%s: %s" % (url, last_error))


def quote(value):
    return urllib.parse.quote(str(value), safe="")
