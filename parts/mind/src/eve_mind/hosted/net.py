"""The one door to the network. Every hosted call goes through a `transport(method, url, headers, body, stream,
timeout)` returning a Reply; tests pass a fake one. The default uses urllib (standard library). Tokens travel only in
the Authorization header or a form body, never in a URL."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlencode


class Reply:
    """status, headers (lower-case keys), and either body (bytes) or lines (an iterator of decoded text lines)."""

    def __init__(self, status, headers=None, body=b"", lines=None):
        self.status, self.headers, self.body, self.lines = status, {k.lower(): v for k, v in (headers or {}).items()}, \
            body, lines

    def json(self):
        try:
            return json.loads(self.body or b"{}")
        except ValueError:
            return {}


class NetError(Exception):
    """No answer at all (connection refused, DNS, TLS, timeout)."""


def urllib_transport(method, url, headers=None, body=None, stream=False, timeout=60.0):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return Reply(e.code, dict(e.headers or {}), e.read() or b"")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise NetError(f"{type(e).__name__}: {e}") from None
    if stream:
        def lines():
            with r:
                for raw in r:
                    yield raw.decode("utf-8", "replace").rstrip("\r\n")
        return Reply(r.status, dict(r.headers), lines=lines())
    with r:
        return Reply(r.status, dict(r.headers), r.read())


def form(transport, url, fields, timeout=30.0):
    return transport("POST", url, {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                     urlencode(fields).encode(), False, timeout)


def get_json(transport, url, bearer=None, timeout=30.0):
    h = {"Accept": "application/json"}
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    return transport("GET", url, h, None, False, timeout)
