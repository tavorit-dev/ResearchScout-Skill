"""URL normalization for the first deduplication layer (spec 10.1).

Pure string functions: no network, no file system. Lowercases scheme and host,
drops default ports and fragments, strips tracking parameters, sorts the
remaining query parameters, and removes a trailing slash.
"""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": "80", "https": "443"}

# A parameter is a tracking parameter when its name starts with one of these
# prefixes, or appears in TRACKING_PARAMS.
TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source", "spm",
})

# `/article/123` is the path form of `/article?id=123`; both collapse to the
# query form so that one article keeps one identity.
ARTICLE_ID_RE = r"^/article/(?P<id>\d+)$"


def _is_tracking_param(name):
    """Return True if the query parameter carries no document identity."""
    return name.startswith(TRACKING_PARAM_PREFIXES) or name in TRACKING_PARAMS


def _normalize_path(path):
    """Drop a trailing slash, keeping the root path as '/'."""
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/") or "/"
    return path


def normalize_url(url):
    """Return the canonical form of a URL used for deduplication."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    netloc = host
    if parts.port is not None and str(parts.port) != DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"
    if parts.username:
        credentials = parts.username
        if parts.password:
            credentials = f"{credentials}:{parts.password}"
        netloc = f"{credentials}@{netloc}"
    query = urlencode(
        sorted(
            (name, value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_tracking_param(name)
        )
    )
    return urlunsplit(
        (scheme, netloc, _normalize_path(parts.path), query, "")
    )


def origin_of(url):
    """Return ``(scheme, host, port)`` for url, or None when unreadable.

    The origin -- not the host -- is the unit an HTTP consent covers. A
    scheme or port change is a different rules context just as much as a
    different host is, so both redirect checks and the compliance gate compare
    this value rather than hostnames. Default ports are normalised, so
    ``https://e.org`` and ``https://e.org:443`` are one origin.
    """
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError):
        return None
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return None
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return scheme, host, port


def same_origin(left, right):
    """Return True when both URLs name the same scheme, host and port."""
    left_origin = origin_of(left)
    return left_origin is not None and left_origin == origin_of(right)


def url_identity(url):
    """Return the deduplication key for a URL.

    Built on normalize_url, then merges the two shapes of a paged article:
    '/article/<digits>' becomes '/article?id=<digits>', so both spellings
    yield the same identity.
    """
    normalized = normalize_url(url)
    parts = urlsplit(normalized)
    match = re.match(ARTICLE_ID_RE, parts.path)
    if match and not parts.query:
        return urlunsplit(
            (parts.scheme, parts.netloc, "/article", f"id={match.group('id')}", "")
        )
    return normalized
