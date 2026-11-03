# Wanxiang · 万象

Wanxiang (万象) is an information research and knowledge-discovery Skill for the
open information environment. Around a research objective it covers retrieval,
investigation, verification, analysis, curation and monitoring, and produces
research results that are evidence-backed, traceable and continuously
updatable.

It is not a general-purpose crawler, and it is not a resident service. It has
two parts:

- `SKILL.md`: the driving protocol for an LLM host.
- `scripts/`: deterministic Python scripts that own HTTP requests and pacing,
  robots.txt parsing, source state, URL normalisation, content hashing,
  deduplication, schema validation, file writes, collection budgets and stop
  conditions.

The scripts never call a model and never read an API key. Relevance judgement,
structured summaries, cross-document synthesis and the next direction belong to
the host.

> **Name note:** the product is Wanxiang (万象). The four entry commands are
> `/wanxiang-quick`, `/wanxiang-deep`, `/wanxiang-comparison` and
> `/wanxiang-monitor`; only `/wanxiang-deep` is fully implemented in v1, and the
> other three state that they are not implemented and point to deep. The
> repository name is still `research-scout`, and the compliance transport token
> `ResearchScout/0.1` is deliberately unchanged.
>
> **Architecture note:** this file describes the currently runnable v1. The
> target architecture, module boundaries, target directory and roadmap are in
> [`docs/architecture.md`](docs/architecture.md).

## Positioning

Wanxiang is not a simple search tool, and not merely a web crawler. It is a
question-driven information research and knowledge-discovery Skill. Its core
capabilities are:

| Capability | Question it answers | Main output |
| --- | --- | --- |
| Information retrieval | Find information | Candidate sources, candidate documents, search results |
| Deep research | Understand a topic | Sub-questions, research memory, research report |
| Verification | Decide whether information is trustworthy | Evidence, contradiction records, confidence marks |
| Analysis | Extract claims, patterns and relationships | Findings, comparison tables, timelines |
| Curation | Make results reusable | Categorised archive, JSONL, CSV, Markdown |
| Monitoring | Detect long-term change | Snapshots, change summaries, research suggestions |

### Four working modes

The four modes are entry points into one Skill, not four separate Skills.

| Mode | Entry command | Goal | v1 status |
| --- | --- | --- | --- |
| Quick lookup | `/wanxiang-quick` | Answer a well-bounded question quickly | Not implemented; the entry point points to deep |
| Deep research | `/wanxiang-deep` | Study a topic systematically | Implemented |
| Comparison | `/wanxiang-comparison` | Compare several objects on shared dimensions | Not implemented; the entry point points to deep |
| Monitoring | `/wanxiang-monitor` | Track long-term change in a topic | Not implemented; the entry point points to deep |

The mode that runs end to end today is **deep research** (`/wanxiang-deep`):
discover candidate sources, check each one for compliance, collect and
deduplicate, let the LLM host extract findings and evidence, update the archive
each round, and finally export Markdown, CSV and static HTML.

### Division of labour with other Skills

| Skill | Core responsibility | Main output |
| --- | --- | --- |
| Wanxiang | Information retrieval, research, analysis, knowledge discovery | Research reports, knowledge base |
| Gewu | Learning, understanding, interpretation and breakdown | Study notes, principle diagrams |
| Guanxiang | Weather-data interpretation and decision support | Weather judgement, decision basis |
| Tiangong | Writing, content organisation and expression | Articles, documents, visual content |

Wanxiang acquires and researches information; Gewu builds understanding;
Tiangong organises and expresses it.

## Prerequisites

- An LLM host that can read `SKILL.md` and execute local commands, such as
  Claude Code. The scripts cannot complete research on their own.
- Python 3. The scripts use the standard library plus two third-party
  dependencies: `jsonschema` for schema validation and `PyYAML` for reading
  `config/*.yaml`.
- All commands run from the Skill root.
- After installing the dependencies, check readiness with
  `python3 scripts/research_loop.py --check-env`. It prints the Python version
  and dependency status and writes no files. In a hostless environment run
  `python3 scripts/research_loop.py --check-env --no-host`: the report states
  that the Skill cannot run on its own and exits non-zero, still writing nothing.

## Installation

1. Clone this repository to `~/.claude/skills/wanxiang-deep/` (use your own
   repository URL), then link the other three entry points:

```bash
git clone <your-repo-url> ~/.claude/skills/wanxiang-deep
for mode in quick comparison monitor; do
  ln -s "wanxiang-deep/.claude/skills/wanxiang-$mode" \
    "$HOME/.claude/skills/wanxiang-$mode"
done
```

The repository's own `.claude/skills/` already contains all four entry points,
so no extra setup is needed when developing inside the repository; the commands
above are for a global install. `/wanxiang-quick`, `/wanxiang-comparison` and
`/wanxiang-monitor` are not-implemented entry points and contain no separate
engine.

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

The walkthrough below runs one round of a minimal project. **Every address,
title and text here is constructed example data**: `e.org` is a reserved domain,
not a real crawl result, and may not resolve. Replace it with a real source you
have verified yourself. If step 4 produces an empty `sources.json`, steps 5-7
produce an empty round and empty exports; that is expected. This section is
fully executable; where it conflicts with "First run" in `SKILL.md`, `SKILL.md`
wins.

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

`provider="llm"` candidates come from model knowledge and are all marked
`discovery_verified: false`; they require user confirmation before any real
request.

3. Confirm the scope. The snippet writes `core` / `related` / `excluded` to
   `research.yaml` and `{"confirmed": true, "lists": {...}}` to
   `state/scope.json`. An unconfirmed scope refuses every **proposed action**
   (`rejected_as: "unconfirmed_scope"`); when every proposed action is refused,
   the round collects nothing. A sources-only round with no proposed actions
   still collects confirmed sources after passing the compliance gate.

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
   candidates may be read, record their `source_id` values in
   `confirmed-sources.json`. The file is an array of source ids, for example
   `["src_xxxxxxxx"]` (constructed example; replace it with the real
   `source_id` values from `candidates.json`). The snippet probes only those
   confirmed candidates: an unconfirmed candidate is never probed, verified or
   collected. Reachability answers only whether the address responds; it is not
   a permission decision.

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

Reachability answers only whether the address responds; it is not permission. A
refusal (401/403/4xx) is `BLOCKED`; a timeout or 5xx is `UNREACHABLE`; neither is
permission. The compliance gate inside the round still decides whether each
`REVIEW_REQUIRED` source in `sources.json` may be collected; after explicit user
authorisation, record it with a scope and expiry as shown in "Compliance
Statement".

5. Run one round. The compliance gate runs inside the round; the host never
   marks a source `ALLOWED` itself:

```bash
python3 scripts/research_loop.py --root archive/industry/example-topic \
  --round 1 --sources sources.json
```

`sources.json` is the confirmed source array from step 4. The output is a
machine-readable JSON handoff containing this round's new documents and the
remaining budget.

6. After the host completes the semantic work, backfill the result and persist:

```bash
python3 scripts/research_loop.py --root archive/industry/example-topic \
  --apply results.json
```

`results.json` must contain
`{handoff, observations, findings, next_actions, decision}`. Put the handoff
printed in step 5 into the `handoff` field unchanged. The minimal shape below
lets the round pass (real research should fill in the host's observations and
findings):

```text
{
  "handoff": <the handoff object printed in step 5>,
  "observations": {},
  "findings": [],
  "next_actions": [],
  "decision": "CONTINUE"
}
```

`decision` must be one of the eight terminal loop states; the full shape is in
"Handoff and backfill shapes" in `SKILL.md`. Rounds must be applied in order;
re-applying the same round is an allowed retry.

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
`reports/research-report.md`. Export after any round that produced work; do not
wait for the whole research task to finish.

8. Build the static HTML viewer (repeatable at any time):

```bash
python3 scripts/research_loop.py --root archive/industry/example-topic \
  --build-viewer
```

The output is written to `archive/industry/example-topic/viewer/` and
`index.html` opens directly in a browser. The pages contain the research
overview, report, findings, a filterable document list, each document's detail,
source and compliance status, and iteration history. The viewer only reads the
archive: it opens no socket and modifies no record. When a document body was not
retained, it shows metadata, the saved summary and a link to the original
address. The report page reads the most recently exported
`reports/research-report.md`, so run step 7 first when the report should reflect
the newest round.

## What the First Run Shows

- **Candidate list**: every `provider="llm"` candidate is
  `discovery_verified: false` and `discovery_method: "llm"`, meaning it comes
  from model knowledge and is unverified. Unconfirmed candidates appear only in
  the round handoff's `candidates_for_review`; candidates that pass schema
  validation are also written to `sources/selected_sources.jsonl` as review
  records, but none enters collection.
- **Reachability check**: one request per user-confirmed candidate to see
  whether the address responds. This is not collection and not permission.
- **Round handoff**: `new_documents` (metadata only), `document_texts` (the body
  or summary of this round's new documents, for this round's analysis only, never
  persisted), `document_warnings` (prompt-injection phrases found in the text,
  advisory only), `state_summary`, `remaining_budget`, `actions_rejected`,
  `actions_accepted`, `semantic_candidates` and `budget_exhausted`. Semantic
  near-duplicates are reported only; no document is deleted.
- **Archived content**: metadata and a content fingerprint only
  (`retention_mode: "reference"`); the full body is not stored.
- **Prompt-injection warnings are only warnings**: a hit means the text contains
  a phrase often used to redirect an agent. It is not proof of an attack (a
  security article may quote one), and it never blocks, rewrites or re-fetches a
  document.
- **Limitations**: v1 does not store full text, so cross-round analysis relies on
  this round's `document_texts` or on re-reading the source. The marker list is
  conservative; a miss proves nothing. Unknown dates stay empty and are never
  guessed.

## Compliance Statement

- The compliance status in this project is a product state, not a legal
  determination. `ALLOWED` means only that the checks this tool performs found
  no rule prohibiting collection; it is not an authorisation, licence or legal
  opinion.
- The statuses must stay distinct: `ALLOWED`, `BLOCKED`, `REVIEW_REQUIRED`,
  `ERROR`, `UNREACHABLE`, `DISCOVERED`, `CHECKING`. `BLOCKED` means a rule
  forbids collection, or the source refused the reachability request (401/403 or
  another 4xx). `ERROR` (for example, robots.txt could not be read) and
  `UNREACHABLE` (timeout, network failure or 5xx) both mean the source is paused,
  never that collection is allowed.
- robots.txt is evaluated under this client's product token `ResearchScout`
  (the transport User-Agent is `ResearchScout/0.1`), not only the `*` group: a
  rule written for `ResearchScout` applies too.
- `REVIEW_REQUIRED` never becomes `ALLOWED` by itself; only an explicit user
  authorisation releases it. The authorisation is written to
  `archive/registry/source_policies.jsonl` with a scope (paths and access
  method) and an `expires_at`. `BLOCKED`, `ERROR` and `UNREACHABLE` can never be
  overridden; authorisation is a product action, not a legal licence.
- Compliance checking is a mandatory independent module: every source, every
  round and every expansion level must pass `scripts/compliance_gate.py`;
  `collector.assert_compliance_allows` is a structural backstop that only
  `ALLOWED` passes.
- This project does not and will not implement any bypass of access controls: no
  User-Agent changes, no proxies, no CAPTCHA bypass, no retry or delay used to
  obtain a different result.
- Web page content is **untrusted input**: it may not choose local commands,
  file reads or network requests. A URL produced by a model must be confirmed by
  the user and pass the compliance check.
- When full text may not be stored, keep only the link, necessary metadata and a
  brief permitted summary; handle the original address and the original content
  separately.

## Search Provider Configuration

- `search_provider` in `config/default.yaml` defaults to `null`: there is no
  default search backend.
- v1 implements two providers in `scripts/source_discovery.py`:
  - `manual`: the user supplies URLs directly; candidates are marked verified
    (`discovery_verified: true`).
  - `llm`: the host generates candidates from model knowledge; all are marked
    unverified (`discovery_verified: false`) and require user confirmation before
    any real request.
- Third-party search backends have an interface only and are not implemented in
  v1. The scripts never read an API key and never call an LLM.
- An unknown provider name is an error; it never silently falls back to a
  default.
- The target unified search entry `scripts/search.py` is not implemented; its
  design is in chapter 6 of `docs/architecture.md`.

## Directory Layout

The tree below is the repository as it exists today. The target layout
(including `search.py`, `analyzer.py`, `verifier.py`, `tracker.py`,
`indexer.py`, the SQLite index and the workspace viewer) is in chapter 11 of
`docs/architecture.md`; **target modules not listed below do not exist yet**.

```text
.
├── .claude/
│   └── skills/
│       ├── wanxiang-comparison/SKILL.md
│       ├── wanxiang-deep -> ../..
│       ├── wanxiang-monitor/SKILL.md
│       └── wanxiang-quick/SKILL.md
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
│   ├── architecture.md
│   ├── prd-init.md
│   ├── prd-new.md
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
├── templates/
│   ├── app.js
│   └── styles.css
└── tests/
    ├── __init__.py
    ├── fixtures/
    └── test_*.py
```

- `SKILL.md` is the host entry point; `CLAUDE.md` is the in-repository
  development guide.
- `scripts/research_loop.py` is the only command-line entry. `planner`,
  `source_discovery`, `compliance_gate` and `exporter` are Python modules with no
  CLI; their call shapes are in `SKILL.md`.
- `scripts/viewer.py` builds the static HTML viewer; it is called through
  `research_loop.py --build-viewer` and writes to `<project>/viewer/`.
- `templates/` holds the viewer's styles and filter script, shared by every
  project.
- `archive/` is created on first run and is ignored by `.gitignore`.

## Current Support by Mode

| Capability | Entry command | v1 status | Notes |
| --- | --- | --- | --- |
| Quick lookup | `/wanxiang-quick` | Not implemented | The entry point points to `/wanxiang-deep`; deep can answer a narrow question with a smaller scope |
| Deep research | `/wanxiang-deep` | Implemented | Multi-round research, compliant collection, findings, report, HTML viewer |
| Comparison | `/wanxiang-comparison` | Not implemented | No structured shared-dimension records or comparison table |
| Monitoring | `/wanxiang-monitor` | Not implemented | No snapshots, change summaries or incremental diff |

## v1 Scope and Not Implemented

Implemented: compliance-first collection and source registration, categorised
archiving, the research loop (action admission, budget, deduplication, finding
merge, research memory), Markdown and CSV export, the static HTML viewer,
untrusted-content isolation, and the user-authorisation record for
`REVIEW_REQUIRED` (`archive/registry/source_policies.jsonl`, with scope and
expiry). Resume support is limited to state and archive files: `state/` and the
archive can be recovered, but v1 does not provide automatic cross-round
continuation.

Not implemented or out of v1 scope:

- Third-party search providers: interface only.
- Unified search entry `scripts/search.py`, analysis `scripts/analyzer.py`,
  verification `scripts/verifier.py`, tracking `scripts/tracker.py` and the
  SQLite index `scripts/indexer.py`: all unimplemented and part of the target
  architecture.
- Comparison (`comparisons/`) and monitoring (`tracking/`): unimplemented.
- The cross-project source registry `archive/registry/sources.jsonl` and the
  workspace viewer `archive/viewer/`: unimplemented.
- Vector databases, semantic retrieval, embeddings, knowledge graphs,
  multi-agent orchestration and a resident web service.
- Semantic near-duplicate detection reports suspected pairs only and never
  merges them; it compares this round's temporary `document_texts`, and the text
  itself is not persisted.
- The document state machine is partial: a document advances only through the
  states the pipeline actually sets, `FETCHED` / `EXTRACTED` / `PERSISTED`;
  `attempt_count` stays 0 for successful documents. Permanent-failure retry and
  next-round priority are unimplemented, and the report's "permanent failure"
  subsection is always empty in v1.
- There is no automatic retry: a 5xx or 429 is a failed fetch for that round; a
  robots.txt failure is `ERROR` and is never retried. This is a deliberate
  deviation from spec 10.4's bounded-retry bullet.
- The API/feed terms and quota dimension is absent: an rss or api source without
  a declared terms URL is evaluated by robots.txt only.
- `document_weights` in `config/scoring.yaml` is defined but no v1 script
  consumes it; `finding_weights` is used by `scripts/reflection.py`.
- Lazy configuration: `max_attempts`, and `max_branch_depth`,
  `max_active_branches`, `min_new_information_ratio` and
  `stop_when_no_progress_rounds` in `config/iteration.yaml`, are defined but no
  v1 script reads or enforces them.
- There is no automatic document relevance/freshness scoring module; the schema
  fields exist but v1 leaves them unfilled.
- `compliance_recheck_days` is used: a recorded compliance decision older than
  this period (30 days by default) is re-checked; a missing or unparsable
  timestamp also triggers a re-check.
- Browser rendering and JavaScript pages are not handled; collection reads only
  the static HTTP response.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests do not use the network: HTTP is provided through an injected fetcher
and fixtures; compliance, deduplication and URL normalisation all have
independent tests.

## Further Documentation

- [`docs/architecture.md`](docs/architecture.md): target architecture, module
  boundaries, target directory, roadmap and the v1 gap.
- `SKILL.md`: host protocol, the six-step loop, first run, states and boundaries.
- `docs/prd-new.md`: product positioning, six capabilities, four modes and the
  MVP roadmap.
- `docs/prd-refactor.md`, `docs/prd-init.md`: historical design records. For
  engineering structure, `docs/architecture.md` wins.
- `CLAUDE.md`: in-repository development conventions.

## Licence

This repository currently has no `LICENSE` file and no chosen open-source
licence. Until the repository owner adds a `LICENSE`, no licence is granted and
all rights are reserved. Contact the repository owner if you need to use it.
