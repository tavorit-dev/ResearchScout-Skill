# 万象 · Wanxiang

万象（Wanxiang）是一个面向开放信息环境的信息研究与知识发现 Skill。它围绕一个
研究目标，完成检索、调研、核验、分析、沉淀与追踪，产出有依据、可追溯、可持续
更新的研究成果。

它不是通用爬虫，也不是常驻服务。它由两部分组成：

- `SKILL.md`：给 LLM 宿主的驱动协议。
- `scripts/`：确定性的 Python 脚本，负责 HTTP 请求与限速、robots.txt 解析、来源
  状态、URL 规范化、内容哈希与去重、Schema 校验、文件写入、采集预算与终止条件。

脚本自身不调用任何 LLM，也不读取 API Key；相关性判断、结构化摘要、跨文档综合与
下一步方向全部由宿主完成。

> **名称说明：** 产品名是「万象 / Wanxiang」。四个入口命令是 `/wanxiang-quick`、
> `/wanxiang-deep`、`/wanxiang-comparison`、`/wanxiang-monitor`；v1 只有
> `/wanxiang-deep` 完整可用，另外三个入口会明确提示未实现并引导到 deep。仓库名
> 为 `ResearchScout-Skill`（本地目录 `research-scout-skill`），合规传输标识
> `ResearchScout/0.1` 有意保留不变。
>
> **架构说明：** 本文描述当前可运行的 v1。目标架构、模块边界、目标目录与演进路线
> 见 [`docs/architecture.md`](docs/architecture.md)。

## 产品定位

万象不是简单的搜索工具，也不只是网页爬虫，而是一个面向问题的信息研究与知识
发现 Skill。核心能力包括：

| 能力 | 解决的问题 | 主要产出 |
| --- | --- | --- |
| 信息检索 | 找到信息 | 候选来源、候选文档、检索结果 |
| 专题调研 | 理解一个主题 | 子问题、研究记忆、研究报告 |
| 信息核验 | 判断信息是否可信 | 证据、冲突记录、可信度标记 |
| 信息分析 | 提取观点、规律与关系 | 发现、对比表、时间线 |
| 知识沉淀 | 让成果可复用 | 分类归档、JSONL、CSV、Markdown |
| 持续追踪 | 发现长期变化 | 快照、变化摘要、研究建议 |

### 四种工作模式

四种模式是同一个 Skill 的不同任务入口，不是四个独立 Skill。

| 模式 | 入口命令 | 目标 | v1 状态 |
| --- | --- | --- | --- |
| 快速检索 | `/wanxiang-quick` | 快速回答一个边界明确的问题 | 未实现；入口提示并引导到 deep |
| 专题调研 | `/wanxiang-deep` | 系统研究一个主题 | 已实现 |
| 对比分析 | `/wanxiang-comparison` | 对多个对象做统一维度比较 | 未实现；入口提示并引导到 deep |
| 持续追踪 | `/wanxiang-monitor` | 关注一个主题的长期变化 | 未实现；入口提示并引导到 deep |

当前可完整跑通的是**专题调研**（`/wanxiang-deep`）：发现候选来源、逐个做合规
检查、采集并去重，由 LLM 宿主抽取发现与证据，每轮更新归档，最后导出 Markdown、
CSV 与静态 HTML。

### 与现有 Skill 的分工

| Skill | 核心职责 | 主要产出 |
| --- | --- | --- |
| 万象 Wanxiang | 信息检索、调研、分析、知识发现 | 研究报告、资料库 |
| 格物 Gewu | 学习、理解、解读、拆解 | 学习笔记、原理图解 |
| 观象 Guanxiang | 天气数据解读与辅助决策 | 天气判断、决策依据 |
| 天工 Tiangong | 写作辅助、内容组织与表达 | 文章、文档、图文内容 |

万象负责获取和研究信息，格物负责深入理解知识，天工负责组织和表达内容。

## 前置条件

- 一个能读取 `SKILL.md` 并执行本地命令的 LLM 宿主，例如 Claude Code。脚本不能
  独立完成研究。
- Python 3。脚本只用标准库加两个第三方依赖：`jsonschema` 用于 Schema 校验，
  `PyYAML` 用于读取 `config/*.yaml`。
- 所有命令都在 Skill 根目录执行。
- 安装依赖后，用 `python3 scripts/research_loop.py --check-env` 确认就绪。它只
  打印 Python 版本与依赖状态，不写任何文件。没有 LLM 宿主时用
  `python3 scripts/research_loop.py --check-env --no-host`：报告会说明本 Skill
  无法独立运行并以非零状态退出，同样不写任何文件。

## 安装

1. 把本仓库克隆到 `~/.claude/skills/wanxiang-deep/`（使用你自己的仓库地址），
   再为另外三个入口建立软链接：

```bash
git clone <your-repo-url> ~/.claude/skills/wanxiang-deep
for mode in quick comparison monitor; do
  ln -s "wanxiang-deep/.claude/skills/wanxiang-$mode" \
    "$HOME/.claude/skills/wanxiang-$mode"
done
```

仓库根的 `.claude/skills/` 已包含这四个入口，在仓库内开发时无需额外配置；上面
命令是给全局安装用的。`/wanxiang-quick`、`/wanxiang-comparison`、
`/wanxiang-monitor` 是未实现提示入口，不包含独立引擎。

2. 建立虚拟环境（推荐，不强制）并安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

3. 验证安装：

```bash
python3 scripts/research_loop.py --check-env
```

脚本不调用模型服务，也不需要 API Key。

## 快速开始（10 分钟）

下面用一个最小项目走完一轮研究。**所有地址、标题与文本都是构造示例**：
`e.org` 是保留域名，不是真实抓取结果，也可能无法解析；请换成你自己确认的真实
来源。如果第 4 步得到空的 `sources.json`，第 5-7 步会得到空轮次与空导出，这是
预期行为。本节给出完整可执行的步骤；与 `SKILL.md` 的「First run」冲突时以
`SKILL.md` 为准。

0. 检查环境：

```bash
python3 scripts/research_loop.py --check-env
```

1. 创建项目。`domain` 与 `topic` 必须是 ASCII slug：

```bash
python3 - <<'PY'
from scripts.planner import init_research

record = init_research(
    "archive",
    domain="industry",
    topic="example-topic",
    objective="示例目标：回答一个具体问题。",
    keywords=["示例关键词"],
    time_window={"recent_days": 90},
    scope={"core": ["示例关键词"], "related": [], "excluded": []},
)
print(record["research_id"])
PY
```

2. 发现候选来源。`discover` 不发起任何请求：

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

`provider="llm"` 的候选来自模型知识，全部标记为 `discovery_verified: false`，
必须由用户确认后才能做真实请求。

3. 确认范围。下面的片段把 `core` / `related` / `excluded` 写入 `research.yaml`，
   并把 `{"confirmed": true, "lists": {...}}` 写入 `state/scope.json`。未确认的
   范围会让每个**被提出的行动**被拒绝（`rejected_as: "unconfirmed_scope"`）；
   当所有被提出的行动都被拒绝时，该轮不采集。没有提出行动的 sources-only 轮次
   仍会在通过合规闸门后采集已确认来源。

```bash
python3 - <<'PY'
import json
from pathlib import Path
import yaml

root = Path("archive/industry/example-topic")
record = yaml.safe_load((root / "research.yaml").read_text(encoding="utf-8"))
record["scope"] = {
    "core": ["示例关键词"],
    "related": ["示例相邻主题"],
    "excluded": ["示例排除主题"],
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

4. 确认来源并做可达性检查。用户确认哪些候选可读后，把它们的 `source_id` 写入
   `confirmed-sources.json`。该文件是一个 source_id 数组，例如
   `["src_xxxxxxxx"]`（构造示例；请替换为 `candidates.json` 中你确认的真实
   `source_id`）。下面的片段只探测这些被确认的候选：未确认的候选不会被探测、
   验证或采集。可达性只回答地址是否响应，不是许可决定。

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
        # 401/403 或其他 4xx：服务器拒绝了请求。
        blocked.append(candidate.get("source_id"))
    else:
        # 超时、网络故障或 5xx：地址没有响应。
        unreachable.append(candidate.get("source_id"))
Path("sources.json").write_text(
    json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print("blocked confirmed sources:", blocked)
print("unreachable confirmed sources:", unreachable)
PY
```

可达性只回答地址是否响应，不是许可决定：拒绝（401/403/4xx）是 `BLOCKED`，
超时或 5xx 是 `UNREACHABLE`，两者都不是允许。轮次内的合规闸门仍会重新决定
`sources.json` 中的每个 `REVIEW_REQUIRED` 来源能否被采集；用户显式授权后可按
「合规声明」中的片段写入带范围与有效期的授权记录。

5. 运行一轮。合规闸门在轮次内部执行，宿主不能自己把来源标成 `ALLOWED`：

```bash
python3 scripts/research_loop.py --root archive/industry/example-topic \
  --round 1 --sources sources.json
```

`sources.json` 是第 4 步生成的已确认来源数组。输出是机器可读的 JSON handoff，
包含本轮新文档与剩余预算。

6. 宿主完成语义工作后，回填结果并持久化：

```bash
python3 scripts/research_loop.py --root archive/industry/example-topic \
  --apply results.json
```

`results.json` 必须包含 `{handoff, observations, findings, next_actions, decision}`。
把第 5 步打印的 handoff 原样放进 `handoff` 字段；最小形状如下（其余键给空值即可
让本轮跑通，真实研究应填入宿主产出的 observations 与 findings）：

```text
{
  "handoff": <第 5 步打印的 handoff 对象>,
  "observations": {},
  "findings": [],
  "next_actions": [],
  "decision": "CONTINUE"
}
```

`decision` 必须是八个循环终止状态之一；完整形状见 `SKILL.md` 的「Handoff and
backfill shapes」。轮次必须按顺序应用；重复应用同一轮是允许的重试。

7. 导出：

```bash
python3 - <<'PY'
from pathlib import Path
from scripts.exporter import export_all

paths = export_all(Path("archive/industry/example-topic"))
print(paths["csv"])
print(paths["report"])
PY
```

`export_all` 生成 `documents/documents.csv` 与中文 Markdown 报告
`reports/research-report.md`。建议每轮产生成果后就导出，不必等整个研究结束。

8. 生成静态 HTML 查看器（可随时重复执行）：

```bash
python3 scripts/research_loop.py --root archive/industry/example-topic \
  --build-viewer
```

输出在 `archive/industry/example-topic/viewer/`，可直接用浏览器打开
`index.html`。页面包含研究总览、报告、发现、可筛选的文档列表、每篇文档的详情、
来源与合规状态、迭代历史。查看器只读取归档，不联网、不改写任何记录；文档正文
未保存时只展示元数据、已保存摘要和原文链接。报告页读取最近一次导出的
`reports/research-report.md`，先执行第 7 步再生成查看器可保证内容最新。

## 首次运行会看到什么

- **候选清单**：`provider="llm"` 的候选全部是 `discovery_verified: false`、
  `discovery_method: "llm"`，表示它们来自模型知识、未经验证。未确认的候选只出现
  在本轮 handoff 的 `candidates_for_review`；通过 Schema 校验的未确认候选还会以
  review 记录写入 `sources/selected_sources.jsonl`，但都不会进入采集。
- **可达性检查**：只对用户确认的候选各发一次请求，判断地址是否响应。这不是采集，
  也不是许可。
- **轮次 handoff**：`new_documents`（仅元数据）、`document_texts`（本轮新文档的
  正文或摘要，仅供本轮分析，不持久化）、`document_warnings`（正文中命中的提示注入
  短语，仅提示）、`state_summary`、`remaining_budget`、`actions_rejected`、
  `actions_accepted`、`semantic_candidates` 与 `budget_exhausted`。语义近似对只
  报告，不删除文档。
- **归档内容**：只有元数据与内容指纹（`retention_mode: "reference"`），不保存
  完整原文。
- **提示注入警告只是提示**：命中说明正文含常见于诱导 Agent 的短语，不等于攻击
  （安全文章也可能引用它们），不会阻断、改写或重采文档。
- **局限**：v1 不保存全文，跨轮分析依赖当轮 `document_texts` 或重新读取来源；
  警告标记列表是保守的，漏报不代表没有风险；日期未知时保持为空，绝不猜测。

## 合规声明

- 本项目的合规状态是产品状态，不是法律许可认定。`ALLOWED` 只表示本工具执行的
  检查没有发现禁止采集的规则，不构成授权、许可或法律意见。
- 状态必须区分：`ALLOWED`、`BLOCKED`、`REVIEW_REQUIRED`、`ERROR`、
  `UNREACHABLE`、`DISCOVERED`、`CHECKING`。`BLOCKED` 表示明确规则禁止采集，或
  来源拒绝可达性请求（401/403 或其他 4xx）；`ERROR`（例如 robots.txt 读不到）与
  `UNREACHABLE`（超时、网络故障或 5xx）都表示来源被暂停，绝不等于允许。
- robots.txt 以本客户端的产品标识 `ResearchScout` 评估（传输 User-Agent 为
  `ResearchScout/0.1`），而不是只读 `*` 组：站点为 `ResearchScout` 写的禁止规则
  同样生效。
- `REVIEW_REQUIRED` 不会自行变成 `ALLOWED`，只能由用户显式授权解除：授权写入
  `archive/registry/source_policies.jsonl`，带授权范围（路径与访问方式）和
  `expires_at`。`BLOCKED`、`ERROR`、`UNREACHABLE` 不能被授权覆盖；授权是产品
  动作，不是法律许可。
- 合规检查是强制独立模块：任何来源、任何轮次、任何扩展层级都必须先过
  `scripts/compliance_gate.py`；`collector.assert_compliance_allows` 是结构性
  兜底，只有 `ALLOWED` 能通过。
- 不提供也不会实现任何绕过访问限制的能力：不更换 User-Agent、不使用代理、不绕
  验证码、不通过重试或延迟获得不同结果。
- 网页正文是**不可信输入**：不能让网页内容决定命令执行、文件读取或网络请求；
  模型生成的 URL 必须经用户确认并通过合规检查。
- 无法保存全文时，只保留链接、必要元数据和符合许可的简短摘要；原文地址与原文
  内容分开处理。

## 搜索 provider 配置

- `config/default.yaml` 的 `search_provider` 默认为 `null`，即没有默认搜索后端。
- v1 在 `scripts/source_discovery.py` 实现两种 provider：
  - `manual`：用户直接给出 URL，候选标记为已验证
    （`discovery_verified: true`）。
  - `llm`：宿主从模型知识生成候选，全部标记为未验证
    （`discovery_verified: false`），用户确认后才做真实请求。
- 第三方搜索后端只有接口，v1 未实现。脚本不会读取 API Key，也不会调用任何 LLM。
- 未知 provider 名会报错，而不是静默回退到某个默认值。
- 目标架构中的统一检索入口 `scripts/search.py` 尚未实现，设计见
  `docs/architecture.md` 第六章。

## 目录结构

以下为当前仓库的实际结构。目标结构（含 `search.py`、`analyzer.py`、
`verifier.py`、`tracker.py`、`indexer.py`、SQLite 索引与工作区查看器）见
`docs/architecture.md` 第十一章；**未列在下面的目标模块目前并不存在**。

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

- `SKILL.md` 是宿主入口；`CLAUDE.md` 是仓库内的开发指引。
- `scripts/research_loop.py` 是唯一的命令行入口；`planner`、
  `source_discovery`、`compliance_gate`、`exporter` 都是模块函数，没有 CLI，
  调用方式见 `SKILL.md`。
- `scripts/viewer.py` 生成静态 HTML 查看器；通过
  `research_loop.py --build-viewer` 调用，输出到 `<项目>/viewer/`。
- `templates/` 存放查看器的样式与筛选脚本，所有项目共用一份。
- `archive/` 在首次运行后生成，已在 `.gitignore` 中忽略。

## 四种模式的当前能力

| 能力 | 入口命令 | v1 状态 | 说明 |
| --- | --- | --- | --- |
| 快速检索 | `/wanxiang-quick` | 未实现 | 入口只提示未实现并引导到 `/wanxiang-deep`；deep 可用更小范围回答窄问题 |
| 专题调研 | `/wanxiang-deep` | 已实现 | 多轮研究、合规采集、发现、报告、HTML 查看器 |
| 对比分析 | `/wanxiang-comparison` | 未实现 | 没有统一维度的结构化记录与对比表 |
| 持续追踪 | `/wanxiang-monitor` | 未实现 | 没有快照、变化摘要与增量对比 |

## v1 范围与未实现项

已实现：合规采集与来源登记、分类归档、研究循环（行动准入、预算、去重、发现合并、
研究记忆）、Markdown 与 CSV 导出、静态 HTML 查看器、不可信内容隔离，以及
`REVIEW_REQUIRED` 的用户授权登记（`archive/registry/source_policies.jsonl`，带
范围与有效期）。断点恢复限于状态与归档文件：`state/` 与归档可恢复，但 v1 不提供
自动跨轮续跑。

未实现或不在 v1 范围：

- 第三方搜索 provider：只有接口。
- 统一检索入口 `scripts/search.py`、分析 `scripts/analyzer.py`、核验
  `scripts/verifier.py`、追踪 `scripts/tracker.py`、SQLite 索引
  `scripts/indexer.py`：均未实现，属于目标架构。
- 对比分析（`comparisons/`）与持续追踪（`tracking/`）：未实现。
- 跨项目来源注册表 `archive/registry/sources.jsonl` 与工作区查看器
  `archive/viewer/`：未实现。
- 向量数据库、语义检索、嵌入、知识图谱、多 Agent 编排、常驻 Web 服务。
- 语义近似去重只报告疑似对，不自动合并；它借助本轮 `document_texts` 的临时文本
  参与比较，文本本身不持久化。
- 文档状态机只实现一部分：状态只推进到流水线实际设置的 `FETCHED` / `EXTRACTED` /
  `PERSISTED`；成功文档的 `attempt_count` 恒为 0；永久失败重试与“下一轮优先处理”
  未实现，报告中的「永久失败文档」小节在 v1 中始终为空。
- 自动重试未实现：5xx 或 429 视为该轮的取回失败；robots 检查失败为 `ERROR` 且
  永不重试。这是对 spec 10.4「有限重试」的有意偏离。
- API / Feed 条款与额度维度未实现：未声明 terms URL 的 rss / api 来源只按
  robots.txt 评估。
- `config/scoring.yaml` 的 `document_weights` 已定义，但 v1 没有脚本消费它；发现
  权重 `finding_weights` 由 `scripts/reflection.py` 使用。
- 惰性配置项：`max_attempts`，以及 `config/iteration.yaml` 中的
  `max_branch_depth`、`max_active_branches`、`min_new_information_ratio`、
  `stop_when_no_progress_rounds` 已定义，但 v1 没有脚本读取或执行。
- 文档级相关性/时效性等评分没有自动计算模块；相关 Schema 字段存在，但 v1 不填。
- `compliance_recheck_days` 已使用：记录的合规决定超过该期限（默认 30 天）后会
  重新检查；时间戳缺失或无法解析同样会触发重查。
- 浏览器渲染与 JavaScript 页面不处理；采集只读取 HTTP 响应中的静态内容。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试不联网：HTTP 通过注入的 fetcher 与 fixture 完成；合规、去重、URL 规范化等
确定性逻辑都有独立测试。

## 更多文档

- [`docs/architecture.md`](docs/architecture.md)：目标架构、模块边界、目标目录、
  演进路线与 v1 差距。
- `SKILL.md`：宿主协议、六步循环、First run、状态与边界。
- `docs/prd-new.md`：产品定位、六项能力、四种模式与 MVP 路线。
- `docs/prd-refactor.md`、`docs/prd-init.md`：历史设计记录。工程结构以
  `docs/architecture.md` 为准。
- `CLAUDE.md`：仓库开发约定。

## 许可

本仓库目前没有 `LICENSE` 文件，也尚未选定开源许可证。在仓库所有者添加 `LICENSE`
之前，本仓库未授予任何许可证，保留所有权利。如需使用，请联系仓库所有者。
