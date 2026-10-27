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

from scripts.collector import normalise_path
from scripts.lib.http import ROBOTS_AGENT, fetch
from scripts.lib.policy import POLICIES_FILE, resolve_policy
from scripts.lib.robots import evaluate_robots, parse_robots
from scripts.lib.urlnorm import same_origin

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

    Callers may pass the full loaded config or a flat mapping of transport
    settings. The nested lookup under ``iteration_policy`` is defensive: the
    transport keys live at the top level of ``config/default.yaml``, while
    ``config/iteration.yaml`` merges under ``iteration_policy``, so the flat
    branch is the one that normally answers. It is kept so a caller that
    supplies either shape gets the same limits.
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


def _address_evidence(base_url):
    """Record the address this decision was made for.

    A decision is consent for an address, not for a host: scheme, port and
    base path are all part of what robots.txt was consulted about. The source
    record's own ``base_url`` can be edited later, so the checked address is
    recorded as evidence; ``research_loop`` reuses a decision only when it
    still matches this value.
    """
    return _evidence(
        "base_url",
        base_url,
        "the address this compliance decision was made for",
        "a recorded decision is reused only for this address",
    )


def _default_fetcher(url, *, timeout, max_bytes, delay):
    """Fetch through the real transport, applying the configured limits."""
    return fetch(url, timeout=timeout, max_bytes=max_bytes, delay=delay)


def _configured_delay(config):
    """Return the request delay configured for a source, before robots.txt."""
    return _config_value(config, "request_delay_seconds", DEFAULT_DELAY)


def _check_once(fetcher, url, config, delay=None):
    """Request url once with the configured limits.

    ``delay`` overrides the configured request delay. The gate passes the
    effective pacing for the source here, so that a published Crawl-delay is
    the interval actually used on the wire and not merely a number in the
    record.
    """
    return fetcher(
        url,
        timeout=_config_value(config, "request_timeout_seconds", DEFAULT_TIMEOUT),
        max_bytes=_config_value(config, "max_response_bytes", DEFAULT_MAX_BYTES),
        delay=_configured_delay(config) if delay is None else delay,
    )


def check_reachability(source, *, fetcher=None, config=None):
    """Return REACHABLE when the source's base URL answers, else a refusal answer.

    This answers "does this address answer us", nothing more (spec 8.4). The
    answers stay distinct because the gate maps them to different statuses:

    * ``REACHABLE`` -- the address answered.
    * ``BLOCKED`` -- the server returned 401, 403 or another 4xx. That is an
      access refusal, not a missing page: the source is blocked, not merely
      unreachable. The gate does not consult robots.txt for it, because a
      refusal to answer already forbids collection.
    * ``UNREACHABLE`` -- a timeout, a network failure or a 5xx. The address did
      not answer; that says nothing about permission and never allows in.
    """
    source = source or {}
    fetcher = fetcher or _default_fetcher
    base_url = source.get("base_url") or ""
    if not base_url:
        return UNREACHABLE
    result = _check_once(fetcher, base_url, config)
    outcome = (result or {}).get("outcome")
    if outcome == "ok":
        return REACHABLE
    if outcome == "blocked":
        return STATUS_BLOCKED
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


def _robot_path(url):
    """Return the normalised path of ``url``, or None when unreadable.

    This is the **one** path the gate decides on and records. It calls
    ``collector.normalise_path`` so the gate and the collector cannot disagree
    about the address being asked about: percent-encoded segments are decoded
    and dot segments removed before robots.txt is consulted, and the value
    written to ``allowed_paths`` is the same normalised path the collector
    will enforce. Evaluating the raw path here was how ``/%73ecret`` and
    ``/public/../secret`` passed the gate and were then fetched.

    ``None`` means the path cannot be read safely (a non-string, an
    unparseable URL, or a segment that decodes to contain ``/``). The callers
    treat that as a refusal, never as the root path.
    """
    if not isinstance(url, str):
        return None
    try:
        raw_path = urlsplit(url).path or ROOT_PATH
    except ValueError:
        return None
    return normalise_path(raw_path)


def check_compliance(source, *, fetcher=None, config=None, target_url=None,
                     policies=None, now=None):
    """Check one source and return its compliance record (spec 6.7).

    Order is: a declared ``robots_url`` origin check, reachability, robots.txt,
    the status decision, the optional policy release, then the record. The
    decision priority is fixed and conservative:

    1. robots ``error`` -> ERROR: the rules could not be read, so the source is
       suspended rather than assumed unrestricted (spec 6.4).
    2. robots ``blocked`` -> BLOCKED: a refusal is a rule against collection.
    3. robots ``absent`` -> continue, recording the 404 as the basis.
    4. terms check -> REVIEW_REQUIRED when a terms URL is declared, otherwise
       ALLOWED.

    Any robots status this function does not recognise falls to ERROR. The
    gate must not be the place where an unknown answer becomes permission.

    The returned record carries the spec 6.7 check fields only. It omits
    ``base_url`` and ``selection_status``, which ``schemas/source.schema.json``
    requires, so the caller must merge the record into the source it was
    checked for before the result validates as a source record.

    The recorded ``request_rate_limit`` is also applied to this source's
    requests: when robots.txt publishes a Crawl-delay the gate re-requests the
    base URL at that interval, so the pacing reaches the transport instead of
    living only in the record.

    ``target_url`` is the address the collector will actually read (a feed or
    API address, or the base URL for a web source). When it is given, the
    robots decision is made for **that URL's normalised path**, not for the
    base URL's: robots.txt is evaluated per path, and the path that matters is
    the one that will be requested. ``collector.normalise_path`` is used for
    both the decision and the value written to ``allowed_paths``, so an
    encoded or dot-segment address is judged as the path the collector will
    actually enforce. When ``target_url`` is absent the base URL's path is
    evaluated, which is the pre-existing behaviour. The gate still records the
    checked ``base_url`` (as evidence), not the target address: consent is for
    the source's address, and the target is one request within it.

    The target must share the base URL's origin (scheme, host, port). robots.txt
    for ``e.org`` says nothing about ``evil.example``, so an off-origin target
    is marked ``ERROR`` with no recorded path -- the same way a
    misconfiguration is marked -- and no decision marker is written. The
    collector's own off-host refusal is the second line of defence. An explicit
    ``robots_url`` must share the base URL's origin for the same reason: a
    declared rules URL on another origin is never fetched, and the source is
    marked ``ERROR``.

    robots.txt is evaluated **as this client**: the rules are queried with
    ``ROBOTS_AGENT``, the product token the transport sends, not with the
    wildcard group. A site-wide ``User-agent: ResearchScout`` refusal therefore
    blocks the source even when no wildcard group exists, and a wildcard-only
    site still permits it.

    ``policies`` is the user authorisation registry (spec 6.5, V13). It is
    consulted **only** when the computed status is ``REVIEW_REQUIRED``: a live,
    in-scope, unexpired ``ALLOWED`` policy releases the source to ``ALLOWED``
    with an evidence entry naming the registry. ``BLOCKED``, ``ERROR`` and
    ``UNREACHABLE`` are never relaxed by a policy. ``now`` overrides the clock
    for tests; the same instant is used for ``checked_at`` and for expiry.

    Three configuration failures write ``checked_at`` of ``None``: an
    unreadable target path, an off-origin target, and an off-origin declared
    ``robots_url``. No decision was made for those records, so the next round
    must not reuse whatever status they carry.
    """
    source = source or {}
    fetcher = fetcher or _default_fetcher
    base_url = source.get("base_url") or ""
    domain = _domain_of(source, base_url)
    clock = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    checked_at = clock.isoformat()
    access_method = source.get("access_method") or "web"
    allowed_paths = []
    evidence = []
    notes = []
    # The path robots.txt is asked about: the request that will be made when
    # the caller says what it is, the base URL's path otherwise. It is
    # normalised by the same helper the collector uses, so the answer and the
    # recorded permission describe the path that will actually be enforced.
    target = target_url if target_url else base_url
    evaluated_path = _robot_path(target)
    robots_url = _robots_url(source)
    declared_robots = (source or {}).get("robots_url")

    record = {
        "source_id": source.get("source_id"),
        "domain": domain,
        "checked_at": checked_at,
        "robots_status": ROBOTS_NOT_CHECKED,
        "terms_status": TERMS_ABSENT,
        "access_method": access_method,
        "compliance_status": STATUS_UNREACHABLE,
        "allowed_paths": allowed_paths,
        "request_rate_limit": _configured_delay(config),
        "evidence": evidence,
        "notes": "",
    }

    # A path that cannot be normalised cannot be asked about: evaluating the
    # root instead would widen the check to a path the caller never named.
    # This is a configuration failure, not a permission decision, so it carries
    # no decision marker and records no permitted path.
    if evaluated_path is None:
        record["compliance_status"] = STATUS_ERROR
        record["checked_at"] = None
        notes.append(
            f"the path of {target!r} cannot be normalised; robots.txt cannot "
            f"be asked about it and no path is permitted"
        )
        evidence.append(_address_evidence(base_url))
        record["notes"] = "; ".join(notes)
        return record

    # An explicit robots URL on another origin would make the gate consult
    # rules that do not describe this source and then apply them to it. That is
    # a configuration failure, not a permission decision: it is refused before
    # any request is made, with no decision marker.
    if declared_robots and not same_origin(base_url, declared_robots):
        record["compliance_status"] = STATUS_ERROR
        record["checked_at"] = None
        evidence.append(
            _evidence(
                "robots_url",
                declared_robots,
                "declared robots URL is on a different origin from base_url",
                "robots.txt for another origin does not describe this source; "
                "it was not fetched and no path is recorded",
            )
        )
        notes.append(
            f"declared robots_url {declared_robots!r} is on a different origin "
            f"from base URL {base_url!r}; it was not consulted"
        )
        evidence.append(_address_evidence(base_url))
        record["notes"] = "; ".join(notes)
        return record

    # Step 1: reachability. An address that does not answer is not checked
    # further and is not collected from. A refusal (401/403/4xx) is BLOCKED, a
    # transport failure is UNREACHABLE; neither consults robots.txt and neither
    # is ever a permission.
    reachability = check_reachability(source, fetcher=fetcher, config=config)
    if reachability != REACHABLE:
        if reachability == STATUS_BLOCKED:
            record["compliance_status"] = STATUS_BLOCKED
            notes.append(
                "base URL refused the request (401/403 or another 4xx); the "
                "source is BLOCKED and robots.txt was not consulted"
            )
            evidence.append(
                _evidence(
                    "reachability",
                    base_url,
                    "the source refused the reachability request",
                    "an access refusal is a rule against collection; robots.txt "
                    "was not consulted",
                )
            )
        else:
            notes.append(
                "base URL did not answer; the source is marked UNREACHABLE and "
                "does not enter the compliance check"
            )
        evidence.append(_address_evidence(base_url))
        record["notes"] = "; ".join(notes)
        return record

    # Step 2: robots.txt. The rules are not known yet, so this request uses the
    # configured delay.
    robots_result = _check_once(fetcher, robots_url, config)
    robots_status, robots_reason = evaluate_robots(robots_result)
    record["robots_status"] = robots_status
    evidence.append(
        _evidence("robots_txt", robots_url, robots_reason, "basis of the robots decision")
    )
    notes.append(f"robots.txt: {robots_reason}")

    # A published Crawl-delay raises the pacing for this domain; a missing or
    # unusable one leaves the configured default in place. The resolved value
    # is both recorded and applied to the requests the gate makes, so a source
    # that asks to be crawled slowly is not merely labelled as such.
    # The rules are queried as this client, not as the wildcard: a group for
    # "ResearchScout" governs this tool even when no "*" group exists.
    rules = parse_robots((robots_result or {}).get("text") or "")
    crawl_delay = rules.crawl_delay(ROBOTS_AGENT)
    if crawl_delay is not None and crawl_delay > record["request_rate_limit"]:
        record["request_rate_limit"] = crawl_delay
        notes.append(f"robots.txt Crawl-delay sets the request interval to {crawl_delay}s")
    effective_rate_limit = record["request_rate_limit"]

    # Step 3: the decision, in the fixed priority order above. An off-origin
    # target comes first: robots.txt was consulted for the source's origin, and
    # that answer does not cover another origin. No path is recorded for it,
    # and no decision marker is written, because no decision was made about
    # this target. The collector's off-host refusal remains the second line.
    if target_url and not same_origin(base_url, target_url):
        record["compliance_status"] = STATUS_ERROR
        record["checked_at"] = None
        evidence.append(
            _evidence(
                "target_origin",
                target_url,
                "target origin differs from the base URL's origin",
                "robots.txt for the base origin does not cover this target; "
                "no path is recorded",
            )
        )
        notes.append(
            f"target {target_url!r} is on a different origin from base URL "
            f"{base_url!r}; no robots decision was made for it"
        )
    elif robots_status == "error":
        record["compliance_status"] = STATUS_ERROR
        notes.append("the rules could not be read, so the source is suspended (not allowed)")
    elif robots_status == "blocked":
        record["compliance_status"] = STATUS_BLOCKED
        notes.append("robots.txt refuses this request; automatic collection stops")
    elif robots_status == "allowed":
        if not rules.is_allowed(evaluated_path, ROBOTS_AGENT):
            record["compliance_status"] = STATUS_BLOCKED
            notes.append(
                f"robots.txt disallows {evaluated_path} for this source"
            )
        else:
            allowed_paths.append(evaluated_path)
    elif robots_status == "absent":
        allowed_paths.append(evaluated_path)
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

    # Step 5: a user authorisation releases REVIEW_REQUIRED only. The policy is
    # consulted after the gate's own decision, so it can never relax BLOCKED,
    # ERROR or UNREACHABLE, and resolve_policy itself returns only a live,
    # in-scope ALLOWED record.
    if record["compliance_status"] == STATUS_REVIEW_REQUIRED and policies:
        policy = resolve_policy(
            policies,
            source_id=source.get("source_id") or "",
            path=evaluated_path,
            access_method=access_method,
            now=clock,
        )
        if policy is not None:
            record["compliance_status"] = STATUS_ALLOWED
            scope = policy.get("scope") if isinstance(policy.get("scope"), dict) else {}
            evidence.append(
                _evidence(
                    "source_policy",
                    str(POLICIES_FILE),
                    "user authorisation resolved_status=ALLOWED for "
                    f"access_method {access_method!r} and path {evaluated_path!r}; "
                    f"expires_at {scope.get('expires_at')!r}",
                    "a scoped, unexpired user authorisation releases this "
                    "REVIEW_REQUIRED source; it is a product action, not a "
                    "licence",
                )
            )
            notes.append(
                "released by a user authorisation in "
                f"{POLICIES_FILE}"
            )

    # The resolved pacing is applied, not just recorded. The reachability
    # request above ran before the rules were known, so when robots.txt asked
    # for a slower crawl the base URL is requested again at that interval. This
    # is the request the collector's first fetch is spaced against, and it is
    # what makes Crawl-delay operative rather than decorative.
    if effective_rate_limit != _configured_delay(config):
        _check_once(fetcher, base_url, config, delay=effective_rate_limit)
        notes.append(
            f"request interval {effective_rate_limit}s applied to this source's requests"
        )

    evidence.append(_address_evidence(base_url))
    record["notes"] = "; ".join(notes)
    return record
