"""Source discovery: how candidate sources enter the system (spec 8).

Discovery produces candidates. It does not collect, and it does not decide
permission. Three stages are kept apart on purpose (spec 8.6, invariant 2):
discovery proposes sources, selection chooses among them, and collection reads
them -- a source is never crawled recursively the moment it is found. Nothing
in this module opens a socket, so there is nothing here that could bypass an
access restriction.

Search is a pluggable dependency, dispatched by name, because the Skill does
not assume it has web search of its own (spec 3.1 C4). There is deliberately
no default provider (spec 8.2): a caller must name one, and an unknown name is
an error rather than a silent fallback.

Every candidate leaves here with ``compliance_status`` of ``DISCOVERED``. A
discovered source is not a permitted source: only the compliance gate may
record a permission status (invariant 1), and the gate runs before collection.

The model-reported path carries the project's central safety claim (spec 8.3,
spec 8.4, anti-goal A4). A source the model recalled is a memory of a source,
not a confirmed one. ``llm_provider`` therefore forces ``discovery_verified``
to False and ``discovery_method`` to "llm" whatever the caller passes in, and
such a candidate must not reach the compliance check until the user confirms
it. The candidate list is presented to the user together with the statement
that it comes from model knowledge and is unverified.
"""
from urllib.parse import urlsplit

from scripts.lib.ids import new_id
from scripts.lib.urlnorm import DEFAULT_PORTS

# Candidate-source defaults (spec 8.5).
STATUS_DISCOVERED = "DISCOVERED"
SELECTION_PENDING = "pending"
ACCESS_METHOD_WEB = "web"
AUTHORITY_UNKNOWN = "unknown"

DISCOVERY_METHOD_MANUAL = "manual"
DISCOVERY_METHOD_LLM = "llm"

DEFAULT_LIMIT = 20


def _base_url_of(url):
    """Return scheme + host of url, or "" when url is unusable.

    The base URL is the unit a source is identified and deduplicated by, so
    the path, query and fragment of a discovered address are dropped here.
    Malformed input returns "" rather than raising, because the input to this
    module is not trusted: a discovered address may be user-pasted or, on the
    model-reported path, invented. An address that cannot be read yields no
    candidate instead of an exception.

    A default port is dropped and a non-default one is kept, reusing the
    definition in ``urlnorm`` so the two modules cannot drift: the base URL is
    the identity unit, so ``https://e.org:443/a`` and ``https://e.org/b`` must
    be one source, while a service on another port is a different source.
    """
    try:
        parts = urlsplit(url or "")
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    if not parts.scheme or not host:
        return ""
    scheme = parts.scheme.lower()
    netloc = host
    if port is not None and str(port) != DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"
    return f"{scheme}://{netloc}"


def _candidate(source_id, base_url, source_name, discovery_method, discovery_verified):
    """Build one candidate source with the spec 8.5 fields this stage produces."""
    return {
        "source_id": source_id,
        "source_name": source_name,
        "base_url": base_url,
        "source_type": "",
        "industry_tags": [],
        "topic_tags": [],
        "authority_level": AUTHORITY_UNKNOWN,
        "discovery_method": discovery_method,
        "discovery_verified": discovery_verified,
        "robots_url": f"{base_url}/robots.txt",
        "terms_url": "",
        "compliance_status": STATUS_DISCOVERED,
        "allowed_paths": [],
        "access_method": ACCESS_METHOD_WEB,
        "last_checked_at": None,
        "selection_status": SELECTION_PENDING,
    }


def _apply_limit(candidates, limit):
    """Return at most ``limit`` candidates, applied after deduplication.

    ``None`` means the caller did not state a cap, and is read as the default
    cap rather than as "no cap": ``None`` is the accidental value in this
    codebase (``config/default.yaml`` carries ``search_provider: null``), and
    an accidentally unbounded candidate list is the wrong way to fail on a
    path that feeds the compliance gate. A non-positive limit asks for no
    candidates and gets none.
    """
    if limit is None:
        limit = DEFAULT_LIMIT
    if limit <= 0:
        return []
    return candidates[:limit]


def _dedupe_by_base_url(candidates):
    """Keep the first candidate seen for each base URL, preserving order.

    Deduplication runs before the cap, so a caller asking for N candidates
    gets N distinct sources rather than N raw entries that collapse to fewer.
    """
    seen = set()
    kept = []
    for candidate in candidates:
        base_url = candidate["base_url"]
        if base_url in seen:
            continue
        seen.add(base_url)
        kept.append(candidate)
    return kept


def manual_provider(urls, *, limit=None, **_):
    """Build candidates from URLs the user supplied directly.

    The scheme and host become ``base_url`` and the host becomes
    ``source_name``, so a feed address such as
    ``https://blog.example.org/feed`` is recorded as the source
    ``blog.example.org``. Candidates are deduplicated by base URL, because one
    host is one source however many of its addresses the user pasted.

    These are the only candidates marked ``discovery_verified``: the user named
    the source, so no memory-based claim is involved. Verification is a
    statement about where the source came from, not about whether collection is
    permitted -- ``compliance_status`` is still ``DISCOVERED`` and the gate
    still has to run.
    """
    candidates = []
    for url in urls or []:
        base_url = _base_url_of(url)
        if not base_url:
            continue
        candidates.append(
            _candidate(
                new_id("src"),
                base_url,
                urlsplit(base_url).hostname,
                DISCOVERY_METHOD_MANUAL,
                True,
            )
        )
    return _apply_limit(_dedupe_by_base_url(candidates), limit)


def llm_provider(candidates, *, limit=None, **_):
    """Build candidates the host agent reported from model knowledge.

    The script cannot call a model, so the candidates arrive already formed and
    this function only normalises them. It marks every one of them unverified
    and attributes it to the model, overriding whatever the caller supplied:
    ``discovery_verified`` and ``discovery_method`` describe how the source was
    obtained, and a model cannot make its own recollection verified by saying
    so (spec 8.3, spec 8.4, anti-goal A4).

    These candidates must be confirmed by the user before the compliance check
    runs; until then they are candidates only, never a collection target.
    """
    built = []
    for candidate in candidates or []:
        candidate = candidate or {}
        # The base URL is read through the same parser as the manual path, so
        # a candidate whose address cannot be parsed -- an unreadable port, for
        # one -- is skipped here too rather than raising on model output.
        base_url = _base_url_of(candidate.get("base_url"))
        if not base_url:
            continue
        entry = _candidate(
            new_id("src"),
            base_url,
            candidate.get("source_name") or urlsplit(base_url).hostname,
            DISCOVERY_METHOD_LLM,
            False,
        )
        for field in ("source_type", "industry_tags", "topic_tags", "authority_level"):
            if candidate.get(field) is not None:
                entry[field] = candidate[field]
        built.append(entry)
    return _apply_limit(_dedupe_by_base_url(built), limit)


# The providers this version implements. Third-party search backends are
# defined by the interface above but not implemented in v1 (spec 8.2), so this
# table stays small and explicit. No entry is a default.
PROVIDERS = {
    DISCOVERY_METHOD_MANUAL: manual_provider,
    DISCOVERY_METHOD_LLM: llm_provider,
}


def discover(query, *, provider, payload=None, limit=20):
    """Return candidate sources for query, using the named provider.

    ``provider`` is a key of ``PROVIDERS``; an unknown name raises ValueError
    rather than falling back to a default, because there is no default to fall
    back to (spec 8.2) and quietly choosing one would make the Skill's search
    capability an assumption instead of a configured dependency.

    ``query`` states what is being researched and is the argument a real search
    backend would use; the providers implemented in v1 take their candidates
    from ``payload`` (URLs, or candidate dicts) instead, so they accept the
    query and ignore it. ``limit`` caps the number of candidates returned and
    is forwarded to the provider, which applies it after deduplication, so a
    caller asking for 20 gets 20 distinct sources.

    The result is a candidate list, not a work queue: nothing is fetched and
    nothing is collected here.
    """
    if provider not in PROVIDERS:
        raise ValueError(
            f"unknown search provider: {provider!r}; "
            f"known providers: {sorted(PROVIDERS)}"
        )
    return PROVIDERS[provider](payload, limit=limit)
