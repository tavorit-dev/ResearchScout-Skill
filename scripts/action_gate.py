"""Action admission gate: the program-enforced half of spec 7.7 and 7.8.

A candidate action is proposed by the host agent, which is a language model.
Invariant 8 (CLAUDE.md) and acceptance criterion V6 (spec 12.1) say the
constraints that decide whether that proposal may run must not be left to the
model's own judgement: a model that talks itself into an out-of-scope action
is exactly the failure mode this module exists to make impossible. So the
gate re-checks the proposal against constraints the model does not author --
the confirmed scope lists, the remaining budget, the priority floor, the time
window -- and returns a verdict the caller cannot argue with.

Two invariants this module protects:

1. **Scope is a hard constraint, and only a confirmed scope is a constraint.**
   An action outside ``core``/``related``, or inside ``excluded``, is refused
   and recorded as its own candidate research topic instead of being folded
   into the current knowledge base (invariant 9, V6). But spec 7.8 is equally
   explicit that a scope the Skill merely *suggested* must not be enforced:
   enforcing a guess turns it into a constraint on what the research is
   allowed to look at, without anyone having agreed to it. ``check_scope``
   therefore refuses outright unless ``scope["confirmed"] is True`` -- strict
   identity, not truthiness -- and says so in its reason. ``research.yaml``
   has no ``confirmed`` field by design (``research.schema.json`` is
   ``additionalProperties: false``), so the caller that assembles a scope dict
   from that file alone lands on this path; the confirmation lives in
   ``state/scope.json`` and reaches the gate through ``planner.load_research``.

2. **The check is formal, never semantic.** spec 7.8 decides that ``related``
   membership is verified only as "a non-blank ``scope_justification`` is
   present"; whether the topic is *really* adjacent to the objective is the
   host agent's call. This module does no similarity scoring, no keyword
   expansion and no scope inference, because inventing a relation the user
   never declared is the same error as enforcing an unconfirmed scope.

This is a pure function module. It reads no file, opens no socket, and holds
no state, so it cannot be a path around an access restriction, and its tests
need no network (V9).

Decision order
--------------
When several checks fail at once, exactly one ``rejected_as`` is reported.
``evaluate_action`` reports the *first* failure in this order, and the order
is part of the contract (pinned by ``tests/test_action_gate.py``):

1. ``unconfirmed_scope`` -- an unconfirmed scope has no authority to decide
   anything, so its verdict precedes every other one. Reporting a budget or
   duplicate rejection first would dress up a scope check that never actually
   happened as if it had.
2. ``candidate_topic`` -- the scope verdict: ``excluded``, or a ``related``
   topic without a usable justification, or a topic in neither list. All three
   send the action to a separate candidate topic, so they share one label.
3. ``budget`` -- checked before the remaining cheap checks because it is the
   hard limit invariant 8 names: with no documents left, no action runs
   regardless of how good it looks.
4. ``duplicate`` -- the topic is already being worked on; cheap and certain,
   so it is reported before the softer judgements below.
5. ``low_priority`` -- the action is admissible but not worth its slot now.
6. ``missing_time_window`` -- checked last because an absent window is
   normally filled in from ``ACTION_TIME_WINDOWS``; only a value that cannot
   be a window at all (``None`` outside ``verify``, zero, negative, a bool)
   reaches this verdict.

A caller that wants a different emphasis can call ``check_scope`` directly and
combine it with its own budget arithmetic; ``evaluate_action`` is the
convenience wrapper for the common case.
"""
from scripts.lib.config import ACTION_TIME_WINDOWS, DEFAULTS

# The rejection labels. They are the caller's contract: a caller that wants to
# route a rejected action somewhere (a candidate-topic file, a retry queue)
# switches on these values rather than on the human-readable reason.
REJECT_UNCONFIRMED_SCOPE = "unconfirmed_scope"
REJECT_CANDIDATE_TOPIC = "candidate_topic"
REJECT_DUPLICATE = "duplicate"
REJECT_BUDGET = "budget"
REJECT_LOW_PRIORITY = "low_priority"
REJECT_MISSING_TIME_WINDOW = "missing_time_window"

# The scope list a topic can be a member of. Membership is decided by exact
# equality, never by substring: see ``_contains``.
LIST_CORE = "core"
LIST_RELATED = "related"
LIST_EXCLUDED = "excluded"

# The one action type whose default time window is unlimited, because finding
# original evidence sometimes means reaching far back (spec 7.5).
UNLIMITED_WINDOW_TYPES = frozenset(
    name for name, days in ACTION_TIME_WINDOWS.items() if days is None
)


def check_scope(action, scope):
    """Return ``(accepted, reason)`` for an action's topic against a scope.

    Checks, in order:

    0. The scope must be confirmed. ``scope.get("confirmed") is not True``
       refuses, with a reason naming the unconfirmed scope. The test is strict
       identity: ``confirmed=1`` and ``confirmed="yes"`` are not a user saying
       yes, and a truthiness check would read a caller's stray value as
       consent. This is the first check because an unconfirmed scope is not a
       weaker constraint but an absent one -- spec 7.8 forbids enforcing a
       suggested scope at all, so there is nothing to check the topic against.
    1. A topic in ``excluded`` is refused, reason names ``excluded``. The
       action becomes a candidate research topic rather than entering the
       current project (spec 7.8, V6).
    2. A topic in ``core`` is accepted.
    3. A topic in ``related`` is accepted only with a non-blank string
       ``scope_justification``. This is the formal check spec 7.8 prescribes;
       whether the topic is semantically adjacent stays the host agent's call.
    4. A topic in no list is refused, reason says ``out of scope``.

    Topics are compared by exact string equality, after stripping surrounding
    whitespace from the action's topic. Substring matching is rejected on
    purpose: it would make "GPU 性能优化与调度" inherit the ``excluded``
    verdict of "GPU 性能优化", a relation the spec never draws -- the spec
    only ever speaks of a topic being in a list. An unmatched near-miss is
    refused as out of scope and can be recorded as a candidate topic, which is
    the conservative direction: the gate never widens a scope on its own.

    Raises
    ------
    AttributeError
        When ``scope`` is not a mapping (``None``, a list, a string, a number).
        ``scope`` is a required argument, so a non-mapping is the caller's
        integration bug, and it is reported as one rather than folded into the
        unconfirmed-scope path. Never read this exception as "unconfirmed
        scope": an unconfirmed scope is a *mapping without* ``confirmed=True``
        and is refused normally, with a reason. Coercing a non-mapping (for
        example ``(scope or {}).get(...)``) would disguise a broken call as an
        ordinary business rejection and blame the user for not confirming a
        scope that was never passed.
    """
    if scope.get("confirmed") is not True:
        return False, (
            "unconfirmed scope: the scope lists have not been confirmed by the "
            "user, so they cannot be used as a hard constraint (spec 7.8); "
            "confirm the scope before evaluating actions"
        )

    topic = _topic_of(action)
    if _contains(scope.get(LIST_EXCLUDED), topic):
        return False, (
            f"topic {topic!r} is in the excluded scope: rejected and recorded "
            f"as a separate candidate research topic"
        )
    if _contains(scope.get(LIST_CORE), topic):
        return True, f"topic {topic!r} is in the core scope"
    if _contains(scope.get(LIST_RELATED), topic):
        if not _has_justification(action.get("scope_justification")):
            return False, (
                f"topic {topic!r} is in the related scope but carries no "
                f"non-blank scope_justification: the link to the core objective "
                f"is unstated, so the action is not admitted (spec 7.8)"
            )
        return True, f"topic {topic!r} is in the related scope with a justification"
    return False, (
        f"topic {topic!r} is out of scope: it is in neither the core nor the "
        f"related list, so it is recorded as a separate candidate research topic"
    )


def evaluate_action(action, *, scope, budget, seen_topics, min_priority=None):
    """Return the admission verdict for one candidate action.

    The verdict is ``{"accepted": bool, "reason": str, "rejected_as": str | None}``.
    ``reason`` is always a readable sentence and is never blank, including on
    success; ``rejected_as`` is ``None`` exactly when ``accepted`` is True.
    On failure it is one of the labels documented in the module docstring, and
    the first failing check in that documented order wins.

    ``check_scope`` runs first, so a scope that is not confirmed refuses
    everything. Then:

    - ``budget["remaining_documents"] > 0``. A missing key counts as zero: a
      caller that never worked out the remaining budget cannot claim there is
      room for another action, and reading an absent key as unlimited would
      make the hard limit optional for anyone who forgot to pass it.
    - the topic must not be in ``seen_topics``. The comparison is exact
      equality, matching ``check_scope``: a topic already under research is
      duplicated work, but a differently worded topic is not assumed to be
      the same one.
    - ``priority >= min_priority``. ``min_priority`` defaults to
      ``DEFAULTS["min_action_priority"]`` (60). A non-integer priority cannot
      satisfy an integer floor, so it fails this check rather than crashing.
    - a usable time window. ``verify`` may have none -- its default window is
      ``None``, because original evidence is allowed to be old (spec 7.5). Any
      other type must carry a positive integer number of days. A missing
      ``time_window_days`` key is *filled in* from ``ACTION_TIME_WINDOWS``
      rather than refused: the window is a required field of the action
      record, so its absence is a caller that has not materialised the default
      yet, not a proposal of an unlimited search. A present-but-unusable value
      (``None``, ``0``, negative, a bool) is refused, because ``0`` means "no
      window", not "a window of zero days".

    Raises
    ------
    AttributeError
        When ``scope`` or ``action`` is not a mapping (``None``, a list, a
        string, a number). Both are required arguments, so a non-mapping is the
        caller's integration bug and is reported as one. This must never be
        read as "unconfirmed scope": an unconfirmed scope is a *mapping
        without* ``confirmed=True`` and is refused normally, with
        ``rejected_as == REJECT_UNCONFIRMED_SCOPE``. A non-mapping ``action``
        with an unconfirmed ``scope`` still refuses on the scope first, because
        the scope guard runs before the action is read at all -- so the
        exception is only ever raised once the scope has been accepted.
    """
    accepted, reason = check_scope(action, scope)
    if not accepted:
        # Deliberate recheck of the confirmation flag, not redundant
        # bookkeeping: ``check_scope`` returns only a reason, so the label has
        # to be derived here from the same condition. The two reads can
        # disagree only if ``scope`` answers differently the second time -- a
        # mapping whose ``get`` is not a pure lookup -- and only in the refusal
        # direction: this read decides the label *after* a refusal has already
        # been returned, so it can relabel a rejection but can never turn one
        # into an admission. Deleting the recheck would not open the gate
        # either, but it would silently downgrade every scope rejection to
        # ``candidate_topic``, including the unconfirmed-scope refusal that
        # spec 7.8 and V6 exist to make visible.
        rejected_as = (
            REJECT_UNCONFIRMED_SCOPE
            if scope.get("confirmed") is not True
            else REJECT_CANDIDATE_TOPIC
        )
        return _rejected(reason, rejected_as)

    if not _has_budget(budget):
        return _rejected(
            "the document budget for this research is exhausted, so no further "
            "action can be admitted",
            REJECT_BUDGET,
        )

    topic = _topic_of(action)
    if topic in _seen_topics(seen_topics):
        return _rejected(
            f"topic {topic!r} has already been researched, so this action "
            f"duplicates existing work",
            REJECT_DUPLICATE,
        )

    if not _meets_priority(action.get("priority"), min_priority):
        floor = _priority_floor(min_priority)
        return _rejected(
            f"priority {action.get('priority')!r} is below the admission floor "
            f"of {floor}",
            REJECT_LOW_PRIORITY,
        )

    ok, window_reason = _has_time_window(action)
    if not ok:
        return _rejected(window_reason, REJECT_MISSING_TIME_WINDOW)

    return {
        "accepted": True,
        "reason": f"action {action.get('action_id')!r} admitted: {reason}",
        "rejected_as": None,
    }


def _rejected(reason, rejected_as):
    """Return a rejection verdict with its label and a readable reason."""
    return {"accepted": False, "reason": reason, "rejected_as": rejected_as}


def _topic_of(action):
    """Return the action's topic as a stripped string, empty when absent."""
    topic = action.get("topic")
    return topic.strip() if isinstance(topic, str) else ""


def _contains(values, topic):
    """Return True when topic is exactly one of values.

    Exact equality is the whole point: see ``check_scope`` for why substring
    matching is refused. A topic that is not a usable string cannot be a list
    member, so an empty topic matches nothing.
    """
    if not topic:
        return False
    return any(value == topic for value in values or [])


def _has_justification(value):
    """Return True when value is a string holding something other than blanks.

    spec 7.8 asks for a *non-empty* justification. ``if not value`` would pass
    a string of spaces, letting an action through the one formal check that
    stands in for the semantic judgement the program does not make.
    """
    return isinstance(value, str) and bool(value.strip())


def _has_budget(budget):
    """Return True when the budget still allows another document.

    A missing or unusable ``remaining_documents`` counts as exhausted, so an
    unset budget can never be read as unlimited headroom.
    """
    remaining = (budget or {}).get("remaining_documents")
    return isinstance(remaining, int) and not isinstance(remaining, bool) and remaining > 0


def _seen_topics(seen_topics):
    """Return the already-researched topics as a set of stripped strings."""
    return {
        topic.strip()
        for topic in seen_topics or []
        if isinstance(topic, str)
    }


def _priority_floor(min_priority):
    """Return the priority floor to apply, defaulting to the config value.

    A caller-supplied floor that is not an integer is ignored in favour of the
    configured default rather than raised on: the floor is a policy knob, and
    comparing an action's priority against a string would abort the whole
    evaluation instead of admitting or refusing the one action.
    """
    if isinstance(min_priority, int) and not isinstance(min_priority, bool):
        return min_priority
    return DEFAULTS["min_action_priority"]


def _meets_priority(priority, min_priority):
    """Return True when priority clears the floor.

    A bool is not a priority even though Python calls it an int, and a missing
    or non-numeric priority cannot clear a numeric floor.
    """
    if not isinstance(priority, int) or isinstance(priority, bool):
        return False
    return priority >= _priority_floor(min_priority)


def _has_time_window(action):
    """Return ``(ok, reason)`` for the action's time window.

    An absent ``time_window_days`` is filled in from ``ACTION_TIME_WINDOWS``;
    a present value must be usable on its own. ``verify`` is the only type
    whose window may be unlimited.

    An action type with no entry in ``ACTION_TIME_WINDOWS`` has no default to
    fall back on, so an absent window is refused rather than admitted. The
    schema's enum lists exactly the four known types, but this module is a
    pure function that a caller may reach before validation, and defaulting an
    unrecognised type to "no window" would grant it the unlimited reach that
    spec 7.5 reserves for ``verify`` alone.
    """
    action_type = action.get("type")
    has_default = action_type in ACTION_TIME_WINDOWS
    if "time_window_days" not in action:
        if not has_default:
            return False, (
                f"action type {action_type!r} has no default time window in "
                f"ACTION_TIME_WINDOWS and none was supplied: every action needs "
                f"a recency window (spec 7.5, V15)"
            )
        default = ACTION_TIME_WINDOWS[action_type]
        if default is None:
            return True, (
                f"action type {action_type!r} needs no time window: its default "
                f"window is unlimited"
            )
        return True, (
            f"time window defaulted to {default} days for action type "
            f"{action_type!r}"
        )

    days = action.get("time_window_days")
    if days is None and action_type in UNLIMITED_WINDOW_TYPES:
        return True, (
            f"action type {action_type!r} may run without a time window; the "
            f"search reaches back as far as the evidence requires"
        )
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        return False, (
            f"time_window_days {days!r} is not a usable recency window for "
            f"action type {action_type!r}: only {sorted(UNLIMITED_WINDOW_TYPES)} "
            f"may omit one, and any other action needs a positive number of days "
            f"(spec 7.5, V15)"
        )
    return True, f"time window of {days} days for action type {action_type!r}"
