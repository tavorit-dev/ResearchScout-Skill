"""robots.txt parsing and outcome classification for the compliance gate.

This module is the robots.txt half of the compliance gate (spec 6.4). It is a
pure string/data module: it performs no HTTP request and no file access, so it
can be tested without network. The HTTP layer that fetches robots.txt belongs
to the compliance gate itself.

Two independent responsibilities:

- ``parse_robots`` turns robots.txt text into a :class:`RobotsRules` query
  object (RFC 9309 group semantics, longest-match wins).
- ``evaluate_robots`` classifies a fetch result into a gate status. It is
  deliberately conservative: an unreachable robots.txt is never read as
  permission to crawl. A server error or a network failure yields ``error``,
  which suspends the source until the rules can be confirmed again.
"""
import re

# Statuses returned by evaluate_robots.
STATUS_ALLOWED = "allowed"
STATUS_BLOCKED = "blocked"
STATUS_ERROR = "error"
STATUS_ABSENT = "absent"

# A leading byte order mark is not whitespace and would hide the first line.
BOM = "﻿"

# A group starts with one or more User-agent lines followed by rules.
USER_AGENT_RE = re.compile(r"^user-agent\s*:\s*(.*)$", re.IGNORECASE)
DISALLOW_RE = re.compile(r"^disallow\s*:\s*(.*)$", re.IGNORECASE)
ALLOW_RE = re.compile(r"^allow\s*:\s*(.*)$", re.IGNORECASE)
CRAWL_DELAY_RE = re.compile(r"^crawl-delay\s*:\s*(\S+)$", re.IGNORECASE)


class RobotsRules:
    """Parsed robots.txt rules, queried per user-agent.

    Each agent maps to a list of ``(path, allowed)`` rules. A matching rule
    with the longest path wins; when an Allow and a Disallow rule match with
    equal length, Allow wins. When no rule matches, the path is allowed.
    """

    def __init__(self, groups=None):
        """Build a rule set from a mapping of agent to parsed group."""
        self._groups = {}
        for agent, group in (groups or {}).items():
            self._groups[agent] = {
                "rules": list(group.get("rules", ())),
                "crawl_delay": group.get("crawl_delay"),
            }

    def _group_for(self, user_agent):
        """Return the group that applies to user_agent, or None.

        A specific group wins over the wildcard group. The agent token is
        matched case-insensitively and as a substring, so that a group for
        ``ExampleBot`` also covers ``ExampleBot/1.0``.
        """
        token = (user_agent or "").strip().lower()
        if token and token in self._groups:
            return self._groups[token]
        if token:
            for agent, group in self._groups.items():
                if agent != "*" and agent in token:
                    return group
        return self._groups.get("*")

    def is_allowed(self, path, user_agent="*"):
        """Return True when path may be fetched by user_agent."""
        group = self._group_for(user_agent)
        if group is None:
            return True
        best_length = -1
        best_allowed = True
        for rule_path, allowed in group["rules"]:
            if not rule_path or not path.startswith(rule_path):
                continue
            # Longest match wins; on a tie Allow wins, so an equal-length
            # Allow rule replaces the Disallow decision.
            if len(rule_path) > best_length or (len(rule_path) == best_length and allowed):
                best_length = len(rule_path)
                best_allowed = allowed
        return best_allowed

    def crawl_delay(self, user_agent="*"):
        """Return the crawl delay in seconds, or None when unspecified."""
        group = self._group_for(user_agent)
        if group is None:
            return None
        return group["crawl_delay"]


def _new_group():
    """Return an empty rule group."""
    return {"rules": [], "crawl_delay": None}


def _parse_crawl_delay(raw):
    """Return raw as a float, or None when it is not a number."""
    try:
        return float(raw)
    except ValueError:
        return None


def parse_robots(text):
    """Parse robots.txt text into a RobotsRules query object.

    Lines are grouped by User-agent; consecutive User-agent lines share one
    group. Unknown directives, comments and blank lines are ignored. Paths
    are compared by prefix, as RFC 9309 specifies.

    A leading byte order mark is removed first (RFC 9309 section 2.2.2). It is
    not whitespace, so it would otherwise hide the first directive from the
    regexes and silently discard the rules that follow.
    """
    groups = {}
    agents = []
    group = None
    for raw_line in (text or "").lstrip(BOM).splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = USER_AGENT_RE.match(line)
        if match:
            agent = match.group(1).strip().lower()
            # A new User-agent line after rules opens the next group.
            if group is None or group["rules"] or group["crawl_delay"] is not None:
                group = _new_group()
                agents = []
            agents.append(agent)
            groups.setdefault(agent, group)
            continue
        if group is None:
            # A rule before any User-agent line belongs to no group.
            continue
        delay_match = CRAWL_DELAY_RE.match(line)
        if delay_match:
            delay = _parse_crawl_delay(delay_match.group(1))
            if delay is not None:
                group["crawl_delay"] = delay
            continue
        rule_match = DISALLOW_RE.match(line) or ALLOW_RE.match(line)
        if rule_match:
            # `Disallow:` with an empty value imposes no restriction.
            rule_path = rule_match.group(1).strip()
            if rule_path:
                group["rules"].append((rule_path, line.lower().startswith("allow")))
    return RobotsRules(groups)


def evaluate_robots(fetch_result):
    """Classify a robots.txt fetch result as (status, reason).

    status is one of ``allowed``/``blocked``/``error``/``absent``. The
    conservative rule (spec 6.4) is that only a fetch that actually returned
    rules may yield ``allowed``: a server error or a network failure yields
    ``error`` and suspends the source, never ``allowed``. ``not_found`` means
    no rules were published, so the source is treated as unrestricted with the
    basis recorded in the reason.
    """
    result = fetch_result or {}
    outcome = result.get("outcome")
    status_code = result.get("status")

    if outcome == "ok":
        return STATUS_ALLOWED, "robots.txt fetched, rules parsed"
    if outcome == "not_found":
        return STATUS_ABSENT, "robots.txt not found (404), no rules published"
    if outcome == "blocked":
        return STATUS_BLOCKED, "robots.txt request refused by the server (403)"
    if outcome == "too_large":
        return STATUS_ERROR, "robots.txt exceeds the read limit, rules unknown"
    if outcome == "server_error":
        if status_code is None:
            return STATUS_ERROR, "robots.txt unavailable (server error)"
        return STATUS_ERROR, f"robots.txt unavailable (server error {status_code})"
    if outcome == "timeout":
        return STATUS_ERROR, "robots.txt unreachable (network failure or timeout)"
    return STATUS_ERROR, f"robots.txt not verified (unknown outcome {outcome!r})"
