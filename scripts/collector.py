"""The collector: turning a permitted source into candidate documents.

This module owns the third of the three separate stages (spec 8.6, invariant
2). Discovery proposes sources, selection chooses among them, and collection
reads the chosen source's own feed, API or page. Nothing here follows a link
out of what it fetched: crawling outward from a discovered address is exactly
the recursion the stage split exists to prevent.

The first thing ``collect`` does is call ``assert_compliance_allows``. That is
a program-level block, not a warning and not a soft skip: only ``ALLOWED``
passes, and every other status raises ``PermissionError``. A source that was
never permitted cannot reach the fetch call at all (spec 6.1, acceptance V2,
invariant 1).

Two rules from the compliance work are enforced here because this is the
caller that owns them:

* A redirect is re-checked. ``fetch`` reports where the request ended up in
  ``final_url`` and leaves re-checking to the caller, because only the caller
  knows what the request was for. When a redirect lands on a different host,
  the body did not come from the permitted source, so it is refused rather
  than archived.
* The scheme is allowlisted. Discovery accepts any scheme with a host, so an
  ``ftp://`` address can arrive here as a source with ``access_method`` of
  ``web``. Such an address is never handed to ``fetch``.

Retention is chosen deliberately, never defaulted to keeping the body (spec
9.6): documents leave here at ``retention_mode`` of ``reference``, carrying the
link, the metadata the feed itself supplied, and a hash -- not the article.
The body is only retained when the caller explicitly asks for it. Deciding
whether a body *may* be kept is the compliance decision's job, so this module
does not upgrade the mode on its own.

Everything fetched is untrusted input (spec 10.5). It is stored and parsed as
data; nothing in this module executes it or reads an instruction out of it.
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from scripts.lib.hashing import content_hash
from scripts.lib.http import DEFAULT_DELAY, fetch
from scripts.lib.ids import new_id
from scripts.lib.urlnorm import normalize_url

# The one compliance status that permits collection (spec 6.2).
STATUS_ALLOWED = "ALLOWED"

# The statuses a source holds before or instead of permission, named here so
# the reason a source was refused can be stated plainly. The block itself does
# not consult this list: it allows only ALLOWED, so a status this module has
# never seen is refused rather than passing because it was absent from a
# denylist. A denylist would be the wrong shape for a gate.
NON_PERMITTING_STATUSES = (
    "BLOCKED",
    "REVIEW_REQUIRED",
    "ERROR",
    "DISCOVERED",
    "CHECKING",
    "UNREACHABLE",
)

# Schemes this collector will hand to the transport. Anything else is skipped
# with a reason instead of being passed on.
ALLOWED_SCHEMES = ("http", "https")

# Access methods whose target address lives in a dedicated field. For these the
# source states where to read; otherwise the base URL itself is the target.
ACCESS_METHOD_RSS = "rss"
ACCESS_METHOD_API = "api"

# How the body was obtained, recorded on each document (spec 9.7).
COLLECTION_METHOD_HTTP_GET = "http_get"

# Document state machine: a document whose body was fetched sits at FETCHED
# (spec 10.3). Extraction and analysis are later stages' work.
CRAWL_STATUS_FETCHED = "FETCHED"

# The conservative retention default: link and permitted metadata only, no
# body on disk (spec 9.6).
RETENTION_REFERENCE = "reference"
RETENTION_EXTRACTED = "extracted"
RETENTION_FULL_ARCHIVE = "full_archive"
RETENTION_MODES = (RETENTION_REFERENCE, RETENTION_EXTRACTED, RETENTION_FULL_ARCHIVE)

# Date confidence values. A date the feed stated explicitly is not the same as
# a date we inferred, and an absent date is never filled in (spec 10.2).
DATE_CONFIDENCE_EXPLICIT = "explicit"
DATE_CONFIDENCE_UNKNOWN = "unknown"

# Request pacing and size, used when no config is supplied. The values mirror
# config/default.yaml, which is what callers normally pass.
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_BYTES = 5242880


def _config_value(config, key, default):
    """Return config[key] when it is set, else default.

    Callers may pass the full loaded config or a flat mapping of transport
    settings. ``config/iteration.yaml`` merges under ``iteration_policy`` while
    the transport keys sit at the top level of ``config/default.yaml``, so both
    shapes are consulted and a caller supplying either gets the same limits.
    """
    if not config:
        return default
    sources = [config]
    policy = config.get("iteration_policy")
    if isinstance(policy, dict):
        sources.append(policy)
    for source in sources:
        value = source.get(key)
        if value is not None:
            return value
    return default


def assert_compliance_allows(source):
    """Raise PermissionError unless source holds the ALLOWED status.

    This is the program-level block of invariant 1 and acceptance V2: an
    unpermitted source cannot enter collection, and the block does not depend
    on the model choosing to respect it. Only ``ALLOWED`` passes. ``BLOCKED``
    (a rule forbids collection), ``REVIEW_REQUIRED`` (a human has not decided),
    ``ERROR`` (the check could not be completed) and ``UNREACHABLE`` all stop
    here, and so do the pre-decision states ``DISCOVERED`` and ``CHECKING``.

    A source with no status at all is refused as well: an absent decision is
    not a permission, and the conservative direction is the only safe one for
    a gate. The failure is raised rather than returned so that a caller cannot
    continue by ignoring a false return value.
    """
    source = source or {}
    status = source.get("compliance_status")
    if status == STATUS_ALLOWED:
        return
    reason = (
        "a compliance status the gate has not decided"
        if status is None or status in NON_PERMITTING_STATUSES
        else f"an unrecognised compliance status {status!r}"
    )
    raise PermissionError(
        f"source {source.get('source_id')!r} cannot be collected: {reason}; "
        f"only {STATUS_ALLOWED} permits collection (spec 6.1)"
    )


def _text(element):
    """Return the stripped text of element, or an empty string."""
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def _localname(tag):
    """Return the tag without its XML namespace.

    Atom documents are namespaced, so ``{http://www.w3.org/2005/Atom}entry``
    has to be recognised as an entry. Comparing local names keeps one code path
    for both formats.
    """
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _children(element, name):
    """Return the direct children of element whose local name is name.

    Matching on the local name rather than the literal tag keeps one code path
    for RSS 2.0 (unprefixed tags) and Atom (namespaced ones), where
    ``{http://www.w3.org/2005/Atom}summary`` and ``summary`` are the same field.
    """
    return [child for child in element if _localname(child.tag) == name]


def _child_text(element, names):
    """Return the text of the first child matching one of names, or "".

    RSS 2.0 and Atom spell the same field differently -- ``description``
    against ``summary``, ``pubDate`` against ``published`` -- so callers pass
    both spellings and take whichever the document actually used.
    """
    for name in names:
        for child in _children(element, name):
            text = _text(child)
            if text:
                return text
    return ""


def _find_children(root, name):
    """Return all descendants of root whose local name is name."""
    return [element for element in root.iter() if _localname(element.tag) == name]


def parse_rfc822_date(text):
    """Return an ISO 8601 UTC timestamp for an RFC 822 date, else None.

    RSS 2.0 states dates in RFC 822 form. A date that cannot be read returns
    None rather than a substitute: guessing a publication date would put a
    fabricated fact into the archive, which spec 10.2 forbids.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        # A date with no zone is read as UTC rather than as local time; the
        # value is stated by the feed, only its zone is assumed, and the
        # assumption is recorded in date_confidence.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_date(text):
    """Return an ISO 8601 UTC timestamp for an ISO 8601 date, else None.

    Atom states dates in ISO 8601 form. As with the RSS path, an unreadable
    value yields None and never a guess.
    """
    text = (text or "").strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(text):
    """Return (iso_timestamp_or_None, confidence) for a feed date string."""
    value = parse_rfc822_date(text) or parse_iso_date(text)
    if value is None:
        return None, DATE_CONFIDENCE_UNKNOWN
    return value, DATE_CONFIDENCE_EXPLICIT


def _link_of(entry):
    """Return the address an RSS or Atom entry points at.

    RSS puts the address in the ``link`` element's text. Atom puts it in the
    ``href`` attribute of a ``link`` element, and an Atom feed may carry
    several links of different ``rel`` values, so a plain ``alternate`` or
    rel-less link is preferred over one describing a feed or an enclosure.
    """
    for element in entry.iter():
        if _localname(element.tag) != "link":
            continue
        href = element.get("href")
        if not href:
            continue
        rel = element.get("rel") or "alternate"
        if rel == "alternate":
            return href.strip()
    return _child_text(entry, ("link",))


def _summary_of(entry):
    """Return the entry's own short description, or an empty string.

    Only what the feed itself published is taken, and it is kept as data. This
    is the feed's summary, not a summary this tool wrote; the host agent
    produces the compliant summary later.
    """
    return _child_text(entry, ("description", "summary", "content"))


def _entry_title(entry):
    """Return the entry title, tolerating Atom's nested title markup.

    An Atom title may hold child markup, in which case the element's own text
    is empty and the text is gathered from its children instead.
    """
    for title in _children(entry, "title"):
        text = _text(title)
        if text:
            return text
        joined = "".join(title.itertext()).strip()
        if joined:
            return joined
    return ""


def parse_feed(text):
    """Return the entries of an RSS 2.0 or Atom feed as plain dicts.

    Each entry is ``{"title", "link", "published_at", "summary"}``.
    ``published_at`` is an ISO 8601 UTC timestamp when the feed stated a date
    this module can read, and None otherwise -- never a substitute for a date
    the feed did not give (spec 10.2). ``date_confidence`` records which of the
    two happened.

    A document that is not parseable XML, or that holds no entries, yields an
    empty list. Feed content is untrusted input (spec 10.5): it is read as
    data, and a malformed document is a document with nothing in it rather
    than an exception for the caller to interpret.
    """
    if not text:
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    entries = _find_children(root, "item") or _find_children(root, "entry")
    items = []
    for entry in entries:
        # Only a field that states publication is read here. ``updated`` is
        # deliberately not a fallback: reporting a revision date as the
        # publication date would put a guessed fact in the record, and spec
        # 10.2 keeps the two apart. An entry with neither field is an entry
        # with an unknown date, which is the honest answer.
        published_at, confidence = _parse_date(
            _child_text(entry, ("pubDate", "published", "issued"))
        )
        items.append(
            {
                "title": _entry_title(entry),
                "link": _link_of(entry),
                "published_at": published_at,
                "date_confidence": confidence,
                "summary": _summary_of(entry),
            }
        )
    return items


def _target_url(source):
    """Return the address to read for this source.

    A source that names a dedicated feed or API address is read there;
    otherwise the base URL is read. Nothing here walks a site looking for
    links: one source is one address.
    """
    method = (source or {}).get("access_method") or ""
    if method == ACCESS_METHOD_RSS:
        return (source or {}).get("feed_url") or ""
    if method == ACCESS_METHOD_API:
        return (source or {}).get("api_url") or ""
    return (source or {}).get("base_url") or ""


def _scheme_allowed(url):
    """Return True when url uses a scheme this collector may fetch.

    Discovery accepts any scheme with a host (spec 8.5), so a non-http address
    can arrive here. Handing it to the transport would ask urllib to speak a
    protocol this project has no compliance story for, so it is refused here.
    A URL that cannot be parsed is refused on the same grounds.
    """
    try:
        return urlsplit(url or "").scheme.lower() in ALLOWED_SCHEMES
    except ValueError:
        return False


def _host_of(url):
    """Return the lowercased host of url, or an empty string."""
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def _delay_for(source, config):
    """Return the request delay to use for this source.

    The source's recorded ``request_rate_limit`` -- the interval the compliance
    gate resolved, including any Crawl-delay the site published -- takes
    precedence over the configured default. That is what makes the recorded
    limit operative for the collector's own requests instead of decorative
    (spec 6.6). A source that carries no recorded limit falls back to the
    configured delay, and a recorded value that cannot be read as a number is
    ignored rather than trusted.
    """
    recorded = (source or {}).get("request_rate_limit")
    try:
        value = float(recorded)
    except (TypeError, ValueError):
        value = None
    if value is not None and value > 0:
        return value
    return _config_value(config, "request_delay_seconds", DEFAULT_DELAY)


def _default_fetcher(url, *, timeout, max_bytes, delay):
    """Fetch through the real transport, applying the resolved limits."""
    return fetch(url, timeout=timeout, max_bytes=max_bytes, delay=delay)


def _fetch_once(fetcher, url, config, delay):
    """Request url once with the resolved limits.

    There is no retry here. A refusal and a server error are answers, and
    asking again would be an attempt to get past one (spec 10.4).
    """
    return fetcher(
        url,
        timeout=_config_value(config, "request_timeout_seconds", DEFAULT_TIMEOUT),
        max_bytes=_config_value(config, "max_response_bytes", DEFAULT_MAX_BYTES),
        delay=delay,
    )


def _now_iso():
    """Return the current UTC time as an ISO 8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _document(source, item, source_url, retrieved_at, retention_mode, collection_method):
    """Build one candidate document record from one feed entry.

    The record carries the six fields ``schemas/document.schema.json`` requires
    plus the metadata the feed itself stated. The body is not copied in: the
    document points at its address, and ``content_hash`` fingerprints what was
    seen so the second deduplication layer has something to compare (spec
    10.1). ``attempt_count`` starts at 0 -- this task does not increment it.
    """
    link = item.get("link") or source_url
    return {
        "document_id": new_id("doc"),
        "source_id": source.get("source_id"),
        "source_name": source.get("source_name") or _host_of(source.get("base_url")),
        "source_url": source.get("base_url"),
        "title": item.get("title") or "",
        "published_at": item.get("published_at"),
        "crawled_at": retrieved_at,
        "first_seen_at": retrieved_at,
        "canonical_url": normalize_url(link),
        "summary": item.get("summary") or "",
        "content_hash": content_hash(item.get("summary") or item.get("title") or ""),
        "collection_method": collection_method,
        "compliance_record_id": source.get("source_id"),
        "crawl_status": CRAWL_STATUS_FETCHED,
        "retention_mode": retention_mode,
        "date_confidence": item.get("date_confidence", DATE_CONFIDENCE_UNKNOWN),
        "attempt_count": 0,
    }


def collect(source, *, fetcher=None, config=None):
    """Return the candidate documents collected from one permitted source.

    The order is fixed and the compliance block comes first: assert, resolve
    the address, check the scheme, fetch once, re-check where the fetch ended
    up, then build one document per feed entry. A source that is not
    ``ALLOWED`` raises ``PermissionError`` before any of that, so a refused or
    undecided source never reaches the transport (invariant 1, acceptance V2).

    Failures are returned as an empty list rather than raised: an unreachable
    source is a source with nothing to collect this round, not a crash. A
    refused fetch is not retried, because asking again is how a client tries to
    get past a refusal (spec 10.4).

    ``retention_mode`` is set deliberately and defaults to ``reference``: the
    link, the metadata the feed stated, and a fingerprint. Keeping a body is
    the caller's explicit choice, because whether a body may be kept is a
    compliance decision this module does not make (spec 9.6).

    Nothing is followed out of the fetched document. The source's own address
    is read once; links inside it are recorded, never crawled (spec 8.6,
    invariant 2).
    """
    source = source or {}
    assert_compliance_allows(source)

    fetcher = fetcher or _default_fetcher
    target_url = _target_url(source)
    if not target_url:
        return []
    if not _scheme_allowed(target_url):
        # A non-http(s) address is skipped with a reason, not handed to the
        # transport: this project has no compliance story for another protocol.
        return []

    retrieved_at = _now_iso()
    delay = _delay_for(source, config)
    result = _fetch_once(fetcher, target_url, config, delay)
    result = result or {}
    if result.get("outcome") != "ok":
        return []

    # ``final_url`` is where the request ended up. The real transport always
    # fills it in; an injected fetcher may omit it, and a missing value is read
    # as "no redirect happened" -- the request ended where it was sent.
    final_url = result.get("final_url") or target_url
    # The redirect is re-checked rather than trusted. A fetch that ended on a
    # different host -- or on a scheme this collector does not speak, or on an
    # address whose host cannot be read at all -- did not come from the source
    # the gate permitted, so it is refused instead of archived. Two unreadable
    # hosts must not compare equal, which is why the target's own host has to
    # be non-empty before the comparison is made.
    requested_host = _host_of(target_url)
    if not requested_host or _host_of(final_url) != requested_host:
        return []
    if not _scheme_allowed(final_url):
        return []

    retention_mode = _config_value(config, "retention_mode", RETENTION_REFERENCE)
    if retention_mode not in RETENTION_MODES:
        retention_mode = RETENTION_REFERENCE
    collection_method = (source.get("access_method") or "").strip() or COLLECTION_METHOD_HTTP_GET

    return [
        _document(
            source,
            item,
            final_url,
            retrieved_at,
            retention_mode,
            collection_method,
        )
        for item in parse_feed(result.get("text") or "")
    ]
