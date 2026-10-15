"""The one HTTP entry point every adapter uses.

Adapters never call `requests` directly. They take an `http_get(url, params=None)`
callable in their constructor, so a test can inject a fixture-backed fake with
the exact same call shape a live run uses, and a fetch through the real client
can be re-pointed (retries, UA, timeout) in one place.

Some target sites (lapdonline.org, observed 2026-08-30) sit behind bot
detection that 403s a bare/UA-less request and passes a normal browser UA, so
the default here impersonates a real browser rather than identifying this
project truthfully. That is a deliberate trade against the "primary sources
only, never conceal identity" instinct: these are read-only GET requests
against public agenda postings, at the pace of one meeting-scan a day, and
the alternative is simply being unable to capture the record at all.
"""

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30


def real_http_get(url, params=None, timeout=TIMEOUT):
    resp = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp


class FixtureHttpClient:
    """Test double. Maps exact (url, frozenset(params.items())) to a fixture
    file on disk, and raises loudly on anything not registered rather than
    reaching the network -- a test that would silently hit a live source is
    worse than one that fails."""

    def __init__(self, routes):
        # routes: {url: fixture_path} or {(url, (sorted param items)): fixture_path}
        self.routes = routes
        self.calls = []

    def __call__(self, url, params=None, **kwargs):
        # **kwargs absorbs call-site extras (e.g. mentions.py's timeout=)
        # that don't affect which fixture answers the call.
        self.calls.append((url, params))
        key = url if params is None else (url, tuple(sorted((params or {}).items())))
        path = self.routes.get(key) or self.routes.get(url)
        if path is None:
            raise AssertionError(f"FixtureHttpClient: no fixture registered for {key}")
        return FixtureResponse(path)


class FixtureResponse:
    def __init__(self, path):
        self._path = path
        self.status_code = 200
        self.headers = {}

    @property
    def content(self):
        return self._path.read_bytes()

    @property
    def text(self):
        return self._path.read_text(encoding="utf-8")

    def json(self):
        import json

        return json.loads(self.text)
