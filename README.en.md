# Research Scout

Research Scout is a Skill for iterative information collection and knowledge
retention in industry research. It turns one research topic into a resumable,
traceable local knowledge archive: discover candidate sources, check each one
for compliance, collect and deduplicate, let the LLM host extract findings and
evidence, update the archive every round, and finally export Markdown and CSV.

It is not a general-purpose crawler and not a long-running service. It uses no
database, no vector search and no multi-agent orchestration. It consists of two
parts:

- `SKILL.md`: the driving protocol the LLM host reads.
- `scripts/`: deterministic Python scripts that own HTTP requests and pacing,
  robots.txt parsing, source state, URL normalisation, content hashing and
  deduplication, schema validation, file writes, collection budgets and stop
  conditions.

The scripts never call an LLM and never read an API key. Relevance judgement,
structured summaries, cross-document synthesis and the next direction all
belong to the host.

## Prerequisites

- An LLM host that can read `SKILL.md` and execute local commands, such as
  Claude Code. The scripts cannot complete research on their own.
- Python 3. The scripts use the standard library plus two third-party
  dependencies: `jsonschema` for schema validation and `PyYAML` for reading
  `config/*.yaml`.
- Run every command from the Skill root.
- After installing the dependencies, confirm readiness with
  `python3 scripts/research_loop.py --check-env`. It prints the Python version
  and dependency status and writes no files. In a hostless environment use
  `python3 scripts/research_loop.py --check-env --no-host`: the report states
  that the Skill cannot run on its own and exits non-zero, still writing
  nothing.

## Installation

1. Clone or copy this repository to `~/.claude/skills/research-scout/` (use your
   own repository address).
2. Create a virtual environment (recommended, not required) and install the
   dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

3. Verify the installation:

```bash
python3 scripts/research_loop.py --check-env
```

The scripts do not call a model service and do not need an API key.

## Quick Start (10 minutes)

The example below runs one minimal research round. **Every address, title and
piece of text is constructed**: `e.org` is a reserved domain, not a real crawl
result, and it may not resolve. Replace it with real sources you have confirmed
yourself. If step 4 produces an empty `sources.json`, steps 5-7 yield an empty
round and an empty export; that is expected. This section gives complete,
runnable steps; `SKILL.md` wins on any conflict.

0. Check the environment:

```bash
python3 scripts/research_loop.py --check-env
```

1. Create the project. `domain` and `topic` must be ASCII slugs:

```bash
python3 - <<'PY'
from scripts.planner import init_research

record = init_research(
    "archive",
    domain="industry",
    topic="example-topic",
    objective="Example objective: answer one concrete question.",
    keywords=["example keyword"],
    time_window={"recent_days": 90},
    scope={"core": ["example keyword"], "related": [], "excluded": []},
)
print(record["research_id"])
PY
```

2. Discover candidate sources. `discover` fetches nothing:

```bash
python3 - <<'PY'
import json
from pathlib import Path
from scripts.source_discovery import discover

candidates = discover(
    "example topic",
    provider="llm",
    payload=[{"base_url": "https://e.org", "source_name": "e.org"}],
)
Path("candidates.json").write_text(
    json.dumps(candidates, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(candidates, ensure_ascii=False, indent=2))
PY
```

Candidates from `provider="llm"` come from model knowledge and are all marked
`discovery_verified: false`. They may make a real request only after the user
confirms them.

3. Confirm the scope. The snippet below writes `core` / `related` / `excluded` to
   `research.yaml` and writes `{"confirmed": true, "lists": {...}}` to
   `state/scope.json`. An unconfirmed scope refuses every **proposed action**
   (`rejected_as: "unconfirmed_scope"`); when every proposed action is refused,
   the round collects nothing. A sources-only round with no proposed actions
   still collects the confirmed sources after passing the compliance gate.

```bash
python3 - <<'PY'
import json
from pathlib import Path
import yaml

root = Path("archive/industry/example-topic")
record = yaml.safe_load((root / "research.yaml").read_text(encoding="utf-8"))
record["scope"] = {
    "core": ["example keyword"],
    "related": ["example adjacent topic"],
    "excluded": ["example excluded topic"],
}
(root / "research.yaml").write_text(
    yaml.safe_dump(record, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
(root / "state" / "scope.json").write_text(
    json.dumps(
        {"confirmed": True, "lists": record["scope"]},
        ensure_ascii=False,
        indent=2,
    ) + "\n",
    encoding="utf-8",
)
PY
```

4. Confirm sources and check reachability. After the user confirms which
   candidates may be read, write their `source_id` values to
   `confirmed-sources.json`. The file is an array of source ids, for example
   `["src_xxxxxxxx"]` (a constructed example; replace it with the real
   `source_id` values from `candidates.json` that you confirmed). The snippet
   below probes those candidates only: an unconfirmed candidate is never
   probed, verified or collected. Reachability only says whether an address
   answers; it is not a permission decision.

```bash
python3 - <<'PY'
import json
from pathlib import Path
from scripts.compliance_gate import check_reachability

candidates = json.loads(Path("candidates.json").read_text(encoding="utf-8"))
confirmed = set(json.loads(
    Path("confirmed-sources.json").read_text(encoding="utf-8")
))
kept, unreachable, blocked = [], [], []
for candidate in candidates:
    if candidate.get("source_id") not in confirmed:
        # An unconfirmed candidate is never probed, verified or collected.
        continue
    answer = check_reachability(candidate)
    if answer == "REACHABLE":
        candidate["discovery_verified"] = True
        kept.append(candidate)
    elif answer == "BLOCKED":
        # 401/403 or another 4xx: the server refused the request.
        blocked.append(candidate.get("source_id"))
    else:
        # Timeout, network failure or 5xx: the address did not answer.
        unreachable.append(candidate.get("source_id"))
Path("sources.json").write_text(
    json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print("blocked confirmed sources:", blocked)
print("unreachable confirmed sources:", unreachable)
PY
```

Reachability only says whether the address answers; it is not a permission
decision. A refusal (401/403/4xx) is `BLOCKED`; a timeout or 5xx is
`UNREACHABLE`; neither is permission. The round's gate still decides every
`REVIEW_REQUIRED` source in `sources.json`; after an explicit user
authorisation, record it with the scoped, expiring policy shown in "Compliance
Statement".

5. Run the round. The compliance gate runs inside the round; the host never
   marks a source `ALLOWED` itself:

```bash
python3 scripts/research_loop.py --root archive/industry/example-topic \
  --round 1 --sources sources.json
```

`sources.json` is the array of confirmed sources produced in step 4. The output
is a machine-readable JSON handoff carrying this round's new documents and the
remaining budget.

6. After the host completes the semantic work, backfill the result and persist
   it:

```bash
python3 scripts/research_loop.py --root archive/industry/example-topic \
  --apply results.json
```

`results.json` must contain
`{handoff, observations, findings, next_actions, decision}`. Put the handoff
printed in step 5 verbatim into the `handoff` field; the minimal shape is below
(empty values for the other keys are enough to let the round run, while real
research should fill in the host's observations and findings):

```text
{
  "handoff": <the handoff object printed in step 5>,
  "observations": {},
  "findings": [],
  "next_actions": [],
  "decision": "CONTINUE"
}
```

`decision` must be one of the eight terminal loop states; see "Handoff and
backfill shapes" in `SKILL.md` for the full shape. Rounds must be applied in
order; re-applying the same round is allowed as a retry.

7. Export:

```bash
python3 - <<'PY'
from pathlib import Path
from scripts.exporter import export_all

paths = export_all(Path("archive/industry/example-topic"))
print(paths["csv"])
print(paths["report"])
PY
```

`export_all` writes `documents/documents.csv` and the Chinese Markdown report
`reports/research-report.md`. Export after any round that produced work; there
is no need to wait for the whole research to finish.

## What the First Run Shows

- **Candidate list**: every `provider="llm"` candidate has
  `discovery_verified: false` and `discovery_method: "llm"`, meaning it comes
  from model knowledge and is unverified. Unconfirmed candidates appear only in
  this round's handoff `candidates_for_review`; a schema-valid unconfirmed
  candidate is also written as a review record to
  `sources/selected_sources.jsonl`, but none of them are ever collected.
- **Reachability check**: one request per user-confirmed candidate to see
  whether the address answers. It is not collection, and it is not permission.
- **Round handoff**: `new_documents` (metadata only), `document_texts` (the new
  documents' body or summary, for this round's analysis only, never persisted),
  `document_warnings` (prompt-injection phrases found in the text, advisory
  only), `state_summary`, `remaining_budget`, `actions_rejected`,
  `actions_accepted`, `semantic_candidates` and `budget_exhausted`. Semantic
  near-duplicate pairs are reported only; no document is removed on that
  ground.
- **Archive contents**: metadata and a content fingerprint only
  (`retention_mode: "reference"`); the full text is not stored.
- **Injection warnings are only a hint**: a hit means the text contains a
  phrase commonly used to redirect an agent; it is not proof of an attack (a
  security article may quote one), and it never blocks, rewrites or re-fetches
  a document.
- **Limitations**: v1 stores no full text, so cross-round analysis relies on
  the current round's `document_texts` or on reading the source again; the
  marker list is deliberately conservative, so a miss proves nothing; an
  unknown date stays null and is never guessed.

## Compliance Statement

- The compliance statuses here are product states, not a legal permission
  finding. `ALLOWED` only means the checks this tool performs found nothing
  prohibiting collection; it is not authorisation, a licence or legal advice.
- The statuses must stay distinct: `ALLOWED`, `BLOCKED`, `REVIEW_REQUIRED`,
  `ERROR`, `UNREACHABLE`, `DISCOVERED`, `CHECKING`. `BLOCKED` means a rule
  forbids collection, or the source refused the reachability request (401, 403
  or another 4xx). `ERROR` (for example an unreadable robots.txt) and
  `UNREACHABLE` (timeout, network failure or 5xx) both pause the source and
  never mean allowed.
- robots.txt is evaluated as this client's product token, `ResearchScout`
  (the transport User-Agent is `ResearchScout/0.1`), not only as `*`: a rule
  written for `ResearchScout` applies even when no wildcard group exists.
- `REVIEW_REQUIRED` never becomes `ALLOWED` by itself. Only an explicit user
  authorisation releases it; the authorisation is written to
  `archive/registry/source_policies.jsonl` with a scope (paths and access
  method) and an `expires_at`. `BLOCKED`, `ERROR` and `UNREACHABLE` cannot be
  overridden, and an authorisation is a product action, not a legal licence.
- The compliance check is a mandatory independent module: every source, every
  round and every expansion passes through `scripts/compliance_gate.py` first;
  `collector.assert_compliance_allows` is the structural backstop, and only
  `ALLOWED` passes it.
- No capability to bypass access restrictions is provided or will be
  implemented: no User-Agent changes, no proxies, no CAPTCHA circumvention, no
  retry or delay to obtain a different answer.
- Web page bodies are **untrusted input**: page content may not choose local
  commands, file reads or network requests; a model-generated URL must be
  confirmed by the user and pass the compliance check.
- When the full text may not be stored, only the link, necessary metadata and a
  permitted short summary are kept; the original address and the original
  content are handled separately.

## Search Provider Configuration

- `search_provider` in `config/default.yaml` defaults to `null`, meaning there
  is no default search backend.
- v1 implements two providers in `scripts/source_discovery.py`:
  - `manual`: the user supplies URLs directly, and the candidates are marked
    verified (`discovery_verified: true`).
  - `llm`: the host produces candidates from model knowledge, and all of them
    are marked unverified (`discovery_verified: false`); a real request happens
    only after the user confirms them.
- Third-party search backends exist as an interface only and are not
  implemented in v1. The scripts never read an API key and never call an LLM.
- An unknown provider name raises an error instead of silently falling back to
  a default.

## Directory Layout

```text
.
├── .gitignore
├── SKILL.md
├── CLAUDE.md
├── README.md
├── README.en.md
├── requirements.txt
├── config/
│   ├── default.yaml
│   ├── iteration.yaml
│   └── scoring.yaml
├── docs/
│   ├── prd-init.md
│   ├── prd-refactor.md
│   └── superpowers/
├── schemas/
│   ├── action.schema.json
│   ├── document.schema.json
│   ├── finding.schema.json
│   ├── research.schema.json
│   └── source.schema.json
├── scripts/
│   ├── action_gate.py
│   ├── archive.py
│   ├── collector.py
│   ├── compliance_gate.py
│   ├── content_extractor.py
│   ├── deduplicator.py
│   ├── exporter.py
│   ├── planner.py
│   ├── reflection.py
│   ├── research_loop.py
│   ├── source_discovery.py
│   ├── viewer.py
│   └── lib/
│       ├── __init__.py
│       ├── config.py
│       ├── hashing.py
│       ├── http.py
│       ├── ids.py
│       ├── jsonl.py
│       ├── markdown.py
│       ├── paths.py
│       ├── policy.py
│       ├── render.py
│       ├── robots.py
│       ├── sanitize.py
│       ├── urlnorm.py
│       └── validate.py
└── tests/
    ├── __init__.py
    ├── fixtures/
    └── test_*.py
```

- `SKILL.md` is the host entry point; `CLAUDE.md` holds the repository's
  development guidance.
- `scripts/research_loop.py` is the only command-line entry point; `planner`,
  `source_discovery`, `compliance_gate` and `exporter` are module functions with
  no CLI. See `SKILL.md` for how to call them.
- `scripts/viewer.py` is a Phase 5 placeholder and raises
  `NotImplementedError`.
- `archive/` is created on first run and is ignored by `.gitignore`.

## v1 Scope and Not Implemented

Implemented: compliance-first collection and source registry, classified
archiving, the research loop (action admission, budgets, deduplication, finding
merges, research memory), Markdown and CSV export, untrusted-content isolation,
and the `REVIEW_REQUIRED` user authorisation registry
(`archive/registry/source_policies.jsonl`, with scope and expiry). Resume is
limited to state and archive files: `state/` and the archive can be resumed,
but v1 does not provide automatic cross-run resume.

Not implemented or out of scope for v1:

- Static HTML viewer (Phase 5): `scripts/viewer.py` is a placeholder; there is
  no `templates/` or `viewer/` directory in the repository.
- Third-party search providers: interface only.
- Vector databases, semantic retrieval, embeddings, knowledge graphs,
  multi-agent orchestration and long-running web services.
- Semantic near-duplicate detection reports suspected pairs only and never
  merges them; it compares the round's transient `document_texts`, which are
  never persisted.
- The document state machine is partial: statuses advance only to `FETCHED` /
  `EXTRACTED` / `PERSISTED` as the pipeline actually sets them;
  `attempt_count` stays 0 for successful documents; permanent-failure retry and
  next-round priority are not implemented, so the report's "permanent failure"
  subsection is never populated in v1.
- There is no automatic retry: a 5xx or 429 is a failed fetch for that round,
  and a robots.txt failure is `ERROR` and is never retried. This is a
  deliberate deviation from spec 10.4's bounded-retry bullet.
- The API/feed terms dimension is absent: an rss or api source without a
  declared terms URL is evaluated by robots.txt only.
- `document_weights` in `config/scoring.yaml` is defined but no v1 script
  consumes it; the finding weights `finding_weights` are used by
  `scripts/reflection.py`.
- Inert config keys: `max_attempts` and the branch/no-progress knobs
  `max_branch_depth`, `max_active_branches`, `min_new_information_ratio` and
  `stop_when_no_progress_rounds` are defined in `config/iteration.yaml`, but no
  v1 script reads or enforces them.
- There is no module that computes document-level relevance, freshness or
  authority scores automatically; the related schema fields exist, but v1 does
  not fill them.
- `compliance_recheck_days` is used: a recorded compliance decision older than
  the configured interval (30 days by default) is checked again; a missing or
  malformed timestamp is re-checked as well.
- Browser rendering and JavaScript pages are not handled; collection reads only
  the static content of an HTTP response.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests do not use the network: HTTP runs through injected fetchers and
fixtures; compliance, deduplication, URL normalisation and other deterministic
logic each have their own tests.

## Further Documentation

- `SKILL.md`: host protocol, the six-step loop, First run, states and
  boundaries.
- `docs/prd-refactor.md`, `docs/prd-init.md`: design records; when they
  conflict, `prd-refactor.md` wins.
- `CLAUDE.md`: repository development conventions.

## Licence

This repository currently has no `LICENSE` file and no open-source licence has
been chosen. Until the repository owner adds a `LICENSE`, no licence is granted
and all rights are reserved. Contact the repository owner if you wish to use it.
