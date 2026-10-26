"""HTTP transport for the compliance gate and the collector.

This module owns transport only: timeout, response size cap, per-domain
request spacing and the mapping from transport failures to a small set of
outcomes. It makes no compliance judgement -- deciding whether a response
*permits* collection is the compliance gate's job (spec 6.1).

``fetch`` returns a plain dict so that the gate can be driven by an injected
fetcher in tests, without network (spec 3.1 C3)::

    {"outcome": ..., "status": ..., "text": ..., "final_url": ...}

``outcome`` is one of ``ok``/``not_found``/``server_error``/``timeout``/
``blocked``/``too_large``. Every failure mode maps to one of them, so callers
can treat the outcome set as closed.

There is deliberately no retry here. A 403 or a 5xx is an answer from the
server, and re-requesting it would be an attempt to get past a refusal
(spec 10.4).
"""
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

# A neutral, honest identifier. It names this tool rather than impersonating a
# browser, so an operator reading their logs can tell who called and reach the
# project for questions. Never replace this with a browser User-Agent: rotating
# or spoofing the agent is how a client circumvents an expressed access
# restriction, which this project does not do (spec 10.4). The README must
# document this identifier.
USER_AGENT = "ResearchScout/0.1 (+https://github.com/research-scout)"

# Timeout and size defaults; config/default.yaml overrides them.
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_BYTES = 5242880
DEFAULT_DELAY = 2.0

# The effective delay is floored here. A robots.txt may carry a broken or
# hostile ``Crawl-delay: -5``; without a floor that becomes a negative sleep
# and, through the per-domain timestamp below, a busy-loop against the server.
MIN_DELAY = 0.0

# Largest block read from the socket at once while enforcing the byte cap.
CHUNK_SIZE = 65536

# Per-domain timestamp of the last request, for request spacing.
_LAST_REQUEST = {}


def _domain(url):
    """Return the host of url, or an empty string when it cannot be read."""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def effective_delay(delay):
    """Return the request delay to actually sleep for.

    Values that are not numbers, and values at or below zero, mean "no delay";
    a negative delay is never subtracted from the elapsed time.
    """
    try:
        value = float(delay)
    except (TypeError, ValueError):
        return MIN_DELAY
    return max(value, MIN_DELAY)


def wait_for_slot(domain, delay, *, now=None, sleep=None):
    """Sleep so that requests to domain keep at least delay seconds apart.

    The wait is measured from the last request to the same domain, so a slow
    response already counts towards the interval.
    """
    clock = now or time.monotonic
    pause = sleep or time.sleep
    spacing = effective_delay(delay)
    if not domain or spacing <= 0:
        return
    last = _LAST_REQUEST.get(domain)
    current = clock()
    if last is not None:
        remaining = spacing - (current - last)
        if remaining > 0:
            pause(remaining)
            current = clock()
    _LAST_REQUEST[domain] = current


def _classify_status(status):
    """Map an HTTP status code to an outcome.

    404 means the address has nothing there. 403 and 401 are refusals and map
    to ``blocked``; the caller must stop rather than try to get past them.
    Other 4xx codes are client errors, which are as final as a refusal for our
    purposes, so they map to ``blocked`` as well. 5xx is a server failure.
    """
    if status == 404:
        return "not_found"
    if status >= 500:
        return "server_error"
    return "blocked"


def _read_capped(response, max_bytes):
    """Read at most max_bytes from response.

    Returns ``(text, oversized)``. The cap is applied while reading, not after
    the fact, so an oversized or endless body is never pulled into memory.
    """
    limit = int(max_bytes)
    chunks = []
    total = 0
    while True:
        chunk = response.read(min(CHUNK_SIZE, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            return None, True
        chunks.append(chunk)
    raw = b"".join(chunks)
    charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace"), False


def _result(outcome, status=None, text=None, final_url=None):
    """Build the fetch result dict every outcome shares."""
    return {
        "outcome": outcome,
        "status": status,
        "text": text,
        "final_url": final_url,
    }


def fetch(url, *, timeout=DEFAULT_TIMEOUT, max_bytes=DEFAULT_MAX_BYTES, delay=DEFAULT_DELAY):
    """Fetch url and return its outcome, status, text and final URL.

    The request is spaced against previous requests to the same domain
    (``delay``), bounded by ``timeout``, and its body is read up to
    ``max_bytes``. Redirects are followed by urllib and reported in
    ``final_url``; the caller is responsible for re-checking rules against a
    redirected target.

    Failures are returned, never raised: a caller that has to catch exceptions
    to learn that a site was unreachable would end up treating a failure as an
    unexpected condition instead of as the conservative answer it is.
    """
    domain = _domain(url)
    wait_for_slot(domain, delay)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text, oversized = _read_capped(response, max_bytes)
            final_url = response.geturl()
            if oversized:
                return _result("too_large", response.status, None, final_url)
            return _result("ok", response.status, text, final_url)
    except urllib.error.HTTPError as error:
        return _result(_classify_status(error.code), error.code, None, url)
    except urllib.error.URLError:
        # URLError covers DNS failures, refused connections and the socket
        # timeout the request layer raises; all of them mean "no answer
        # received", which is a transport failure and never a permission.
        return _result("timeout", None, None, url)
    except socket.timeout:
        return _result("timeout", None, None, url)
    except (ValueError, OSError):
        # A malformed URL, or a socket error outside the URLError wrapper.
        return _result("timeout", None, None, url)
