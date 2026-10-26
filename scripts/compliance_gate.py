"""The compliance gate: the one door every source passes before collection.

The gate is an independent module (spec 6.1, invariant 1). It is not a set of
conditions scattered through collection code, and nothing here collects: it
answers two separate questions and returns a record.

1. ``check_reachability`` -- does this address answer at all? It decides
   nothing about permission (spec 8.4).
2. ``check_compliance`` -- is collection from this source permitted, and on
   what basis? It consults robots.txt, then applies the conservative defaults
   of spec 6.4 and assembles the check record of spec 6.7.

The defaults are conservative in one direction only: an unreadable or failed
check suspends the source. ``BLOCKED`` (a rule forbids collection) and
``ERROR`` (the check could not be completed) are never merged, and neither is
ever allowed to fall through to ``ALLOWED`` (spec 6.2).

Compliance status is a product state, not a legal determination (spec 6.8).
``ALLOWED`` means the checks this tool performs found nothing prohibiting
collection; it is not a licence.
"""
from datetime import datetime, timezone
from urllib.parse import urlsplit

from scripts.lib.http import fetch
from scripts.lib.robots import evaluate_robots, parse_robots

# Compliance statuses (spec 6.2). CHECKING is not returned here: it describes a
# source mid-flight, not a decision.
STATUS_ALLOWED = "ALLOWED"
STATUS_BLOCKED = "BLOCKED"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STATUS_ERROR = "ERROR"
STATUS_UNREACHABLE = "UNREACHABLE"

# Reachability answers (spec 8.4).
REACHABLE = "REACHABLE"
UNREACHABLE = "UNREACHABLE"

# robots_status recorded when robots.txt was never consulted, because the
# source did not answer and the gate stopped at step 1.
ROBOTS_NOT_CHECKED = "not_checked"

# terms_status values. v1 does not parse terms pages; it records whether a
# terms URL exists and parks the source for a human to confirm.
TERMS_ABSENT = "absent"
TERMS_PRESENT = "present"

# Request pacing and size, used when no config is supplied. The values mirror
# config/default.yaml, which is what callers normally pass.
DEFAULT_DELAY = 2.0
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_BYTES = 5242880

# Path asked about when deciding whether the source as a whole may be read.
ROOT_PATH = "/"


def _config_value(config, key, default):
    """Return config[key] when it is set, else default.

    Callers may pass the full loaded config (whose iteration policy lives under
    ``iteration_policy``) or a flat mapping of transport settings.
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


def _robots_url(source):
    """Return the robots.txt URL to consult for source.

    An explicit ``robots_url`` wins; otherwise the well-known location under
    the source's base URL is used.
    """
    declared = (source or {}).get("robots_url")
    if declared:
        return declared
    base_url = ((source or {}).get("base_url") or "").rstrip("/")
    return f"{base_url}/robots.txt" if base_url else ""


def _domain_of(source, base_url):
    """Return the domain the record is keyed on.

    The declared domain wins; otherwise it is the host of the base URL, which
    is also the unit the request rate limit is applied to.
    """
    declared = (source or {}).get("domain")
    if declared:
        return declared
    try:
        return urlsplit(base_url or "").hostname or ""
    except ValueError:
        return ""


def _evidence(kind, url, detail, note):
    """Build one evidence entry (spec 6.7).

    Evidence must name the source it was read from, so a later reviewer can
    re-check the decision rather than trust it.
    """
    return {"type": kind, "url": url, "detail": detail, "note": note}


def _default_fetcher(url, *, timeout, max_bytes, delay):
    """Fetch through the real transport, applying the configured limits."""
    return fetch(url, timeout=timeout, max_bytes=max_bytes, delay=delay)


def _check_once(fetcher, url, config):
    """Request url once with the configured limits."""
    return fetcher(
        url,
        timeout=_config_value(config, "request_timeout_seconds", DEFAULT_TIMEOUT),
        max_bytes=_config_value(config, "max_response_bytes", DEFAULT_MAX_BYTES),
        delay=_config_value(config, "request_delay_seconds", DEFAULT_DELAY),
    )


def check_reachability(source, *, fetcher=None, config=None):
    """Return REACHABLE when the source's base URL answers, else UNREACHABLE.

    This answers "does this address exist", nothing more. A refusal, a timeout
    and a server error all mean the address did not answer us, so all three are
    UNREACHABLE; none of them is a statement about permission (spec 8.4), and
    an unreachable source never reaches the compliance check.
    """
    source = source or {}
    fetcher = fetcher or _default_fetcher
    base_url = source.get("base_url") or ""
    if not base_url:
        return UNREACHABLE
    result = _check_once(fetcher, base_url, config)
    if (result or {}).get("outcome") == "ok":
        return REACHABLE
    return UNREACHABLE


def _terms_decision(source):
    """Return (terms_status, compliance_status_or_None, note).

    v1 does not read terms pages. When the source declares a terms URL the
    source is parked at REVIEW_REQUIRED for a human to confirm; otherwise the
    terms dimension adds no restriction and the decision is left to robots.
    """
    terms_url = (source or {}).get("terms_url")
    if terms_url:
        return (
            TERMS_PRESENT,
            STATUS_REVIEW_REQUIRED,
            "a terms URL is declared and v1 does not read terms pages: "
            "a human must confirm whether automated collection is permitted",
        )
    return (
        TERMS_ABSENT,
        None,
        "no terms URL declared, so the terms check adds no restriction",
    )


def _robot_path(base_url):
    """Return the path of base_url, used for the robots.txt rule query."""
    try:
        return urlsplit(base_url or "").path or ROOT_PATH
    except ValueError:
        return ROOT_PATH


def check_compliance(source, *, fetcher=None, config=None):
    """Check one source and return its compliance record (spec 6.7).

    Order is reachability, then robots.txt, then the status decision, then the
    record. The decision priority is fixed and conservative:

    1. robots ``error`` -> ERROR: the rules could not be read, so the source is
       suspended rather than assumed unrestricted (spec 6.4).
    2. robots ``blocked`` -> BLOCKED: a refusal is a rule against collection.
    3. robots ``absent`` -> continue, recording the 404 as the basis.
    4. terms check -> REVIEW_REQUIRED when a terms URL is declared, otherwise
       ALLOWED.

    Any robots status this function does not recognise falls to ERROR. The
    gate must not be the place where an unknown answer becomes permission.
    """
    source = source or {}
    fetcher = fetcher or _default_fetcher
    base_url = source.get("base_url") or ""
    domain = _domain_of(source, base_url)
    checked_at = datetime.now(timezone.utc).isoformat()
    access_method = source.get("access_method") or "web"
    allowed_paths = []
    evidence = []
    notes = []

    record = {
        "source_id": source.get("source_id"),
        "domain": domain,
        "checked_at": checked_at,
        "robots_status": ROBOTS_NOT_CHECKED,
        "terms_status": TERMS_ABSENT,
        "access_method": access_method,
        "compliance_status": STATUS_UNREACHABLE,
        "allowed_paths": allowed_paths,
        "request_rate_limit": _config_value(
            config, "request_delay_seconds", DEFAULT_DELAY
        ),
        "evidence": evidence,
        "notes": "",
    }

    # Step 1: reachability. An address that does not answer is not checked
    # further and is not collected from.
    if check_reachability(source, fetcher=fetcher, config=config) != REACHABLE:
        notes.append(
            "base URL did not answer; the source is marked UNREACHABLE and "
            "does not enter the compliance check"
        )
        record["notes"] = "; ".join(notes)
        return record

    # Step 2: robots.txt.
    robots_url = _robots_url(source)
    robots_result = _check_once(fetcher, robots_url, config)
    robots_status, robots_reason = evaluate_robots(robots_result)
    record["robots_status"] = robots_status
    evidence.append(
        _evidence("robots_txt", robots_url, robots_reason, "basis of the robots decision")
    )
    notes.append(f"robots.txt: {robots_reason}")

    # A published Crawl-delay raises the pacing for this domain; a missing or
    # unusable one leaves the configured default in place.
    rules = parse_robots((robots_result or {}).get("text") or "")
    crawl_delay = rules.crawl_delay()
    if crawl_delay is not None and crawl_delay > record["request_rate_limit"]:
        record["request_rate_limit"] = crawl_delay
        notes.append(f"robots.txt Crawl-delay sets the request interval to {crawl_delay}s")

    # Step 3: the decision, in the fixed priority order above.
    if robots_status == "error":
        record["compliance_status"] = STATUS_ERROR
        notes.append("the rules could not be read, so the source is suspended (not allowed)")
    elif robots_status == "blocked":
        record["compliance_status"] = STATUS_BLOCKED
        notes.append("robots.txt refuses this request; automatic collection stops")
    elif robots_status == "allowed":
        path = _robot_path(base_url)
        if not rules.is_allowed(path):
            record["compliance_status"] = STATUS_BLOCKED
            notes.append(f"robots.txt disallows {path} for this source")
        else:
            allowed_paths.append(path)
    elif robots_status == "absent":
        allowed_paths.append(_robot_path(base_url))
    else:
        # Unrecognised status: fail closed. This branch must never yield
        # ALLOWED, however evaluate_robots evolves.
        record["compliance_status"] = STATUS_ERROR
        notes.append(f"unrecognised robots status {robots_status!r}; failing closed")

    # Step 4: terms check, and the final status when robots did not decide it.
    if record["compliance_status"] == STATUS_UNREACHABLE:
        terms_status, terms_decision, terms_note = _terms_decision(source)
        record["terms_status"] = terms_status
        notes.append(terms_note)
        if terms_status == TERMS_PRESENT:
            terms_url = source.get("terms_url")
            evidence.append(
                _evidence(
                    "terms_page",
                    terms_url,
                    "terms URL declared; contents not read in v1",
                    "parked at REVIEW_REQUIRED until a human confirms",
                )
            )
        record["compliance_status"] = terms_decision or STATUS_ALLOWED

    record["notes"] = "; ".join(notes)
    return record
