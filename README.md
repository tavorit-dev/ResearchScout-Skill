# Research Scout

Research Scout（研究侦察员）是一个面向行业研究的迭代式信息采集与知识沉淀 Skill。
它把一个研究主题变成可断点续跑、可追溯的本地知识库：发现候选来源、逐个做合规
检查、采集并去重，由 LLM 宿主抽取发现与证据，每轮更新归档，最后导出 Markdown
与 CSV。

它不是通用爬虫，也不是常驻服务，没有数据库、向量检索或多 Agent 编排。它由两部分
组成：

- `SKILL.md`：给 LLM 宿主的驱动协议。
- `scripts/`：确定性的 Python 脚本，负责 HTTP 请求与限速、robots.txt 解析、来源
  状态、URL 规范化、内容哈希与去重、Schema 校验、文件写入、采集预算与终止条件。

脚本自身不调用任何 LLM，也不读取 API Key；相关性判断、结构化摘要、跨文档综合与
下一步方向全部由宿主完成。

## 前置条件

- 一个能读取 `SKILL.md` 并执行本地命令的 LLM 宿主，例如 Claude Code。脚本不能
  独立完成研究。
- Python 3。脚本只用标准库加两个第三方依赖：`jsonschema` 用于 Schema 校验，
  `PyYAML` 用于读取 `config/*.yaml`。
- 所有命令都在 Skill 根目录执行。
- 安装依赖后，用 `python3 scripts/research_loop.py --check-env` 确认就绪。它只
  打印 Python 版本与依赖状态，不写任何文件。

## 安装

1. 把本仓库克隆或复制到 `~/.claude/skills/research-scout/`（使用你自己的仓库
   地址）。
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
`e.org` 是保留域名，不是真实抓取结果，也不保证可达；实际使用时请换成你自己确认
的来源。完整的精确片段在 `SKILL.md` 的「First run」一节，本节只做摘要；两者冲突
时以 `SKILL.md` 为准。

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

3. 确认范围。宿主把 `core` / `related` / `excluded` 写入 `research.yaml`，并把
   `{"confirmed": true, "lists": {...}}` 写入 `state/scope.json`；精确片段见
   `SKILL.md` 的「First run」第 3 步。**未确认的范围会让每个行动都被拒绝
   （`rejected_as: "unconfirmed_scope"`），该轮不会采集。**

4. 确认来源并做可达性检查。用户确认哪些候选可读，把 `source_id` 写入
   `confirmed-sources.json`，再只对它们调用 `check_reachability`；精确片段见
   `SKILL.md` 的「First run」第 4 步。未确认的候选不会被探测、验证或采集。可达性
   只回答地址是否响应，不是许可决定。

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

`results.json` 必须包含 `{handoff, observations, findings, next_actions, decision}`，
形状见 `SKILL.md` 的「Handoff and backfill shapes」。轮次必须按顺序应用；重复
应用同一轮是允许的重试。

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

## 首次运行会看到什么

- **候选清单**：`provider="llm"` 的候选全部是 `discovery_verified: false`、
  `discovery_method: "llm"`，表示它们来自模型知识、未经验证。未确认的候选只出现
  在 `candidates_for_review`，不会进入采集。
- **可达性检查**：只对用户确认的候选各发一次请求，判断地址是否响应。这不是采集，
  也不是许可。
- **轮次 handoff**：`new_documents`（仅元数据）、`document_texts`（本轮新文档的
  正文或摘要，仅供本轮分析，不持久化）、`document_warnings`（正文中命中的提示注入
  短语，仅提示）、`state_summary`、`remaining_budget`、`actions_rejected` 与
  `budget_exhausted`。
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
  `UNREACHABLE`、`DISCOVERED`、`CHECKING`。`ERROR`（例如 robots.txt 读不到）与
  `UNREACHABLE` 都表示来源被暂停，绝不等于允许。
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

## 目录结构

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

- `SKILL.md` 是宿主入口；`CLAUDE.md` 是仓库内的开发指引。
- `scripts/research_loop.py` 是唯一的命令行入口；`planner`、
  `source_discovery`、`compliance_gate`、`exporter` 都是模块函数，没有 CLI，
  调用方式见 `SKILL.md`。
- `scripts/viewer.py` 是 Phase 5 占位，调用时抛出 `NotImplementedError`。
- `archive/` 在首次运行后生成，已在 `.gitignore` 中忽略。

## v1 范围与未实现项

已实现：合规采集与来源登记、分类归档、研究循环（行动准入、预算、去重、发现合并、
研究记忆）、Markdown 与 CSV 导出、断点恢复与增量采集、不可信内容隔离。

未实现或不在 v1 范围：

- 静态 HTML 查看器（Phase 5）：`scripts/viewer.py` 只是占位；仓库中没有
  `templates/` 或 `viewer/` 目录。
- 第三方搜索 provider：只有接口。
- 向量数据库、语义检索、嵌入、知识图谱、多 Agent 编排、常驻 Web 服务。
- 语义近似去重只报告疑似对，不自动合并。
- `config/scoring.yaml` 的 `document_weights` 已定义，但 v1 没有脚本消费它；发现
  权重 `finding_weights` 由 `scripts/reflection.py` 使用。
- 文档级相关性/时效性等评分没有自动计算模块；相关 Schema 字段存在，但 v1 不填。
- `compliance_recheck_days` 已定义，v1 尚未使用：同一地址的已记录决定会持续复用。
- 浏览器渲染与 JavaScript 页面不处理；采集只读取 HTTP 响应中的静态内容。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试不联网：HTTP 通过注入的 fetcher 与 fixture 完成；合规、去重、URL 规范化等
确定性逻辑都有独立测试。

## 更多文档

- `SKILL.md`：宿主协议、六步循环、First run、状态与边界。
- `docs/prd-refactor.md`、`docs/prd-init.md`：设计记录，两者冲突时以
  `prd-refactor.md` 为准。
- `CLAUDE.md`：仓库开发约定。

## 许可

本仓库目前没有 `LICENSE` 文件，也尚未选定开源许可证。在仓库所有者添加 `LICENSE`
之前，本仓库未授予任何许可证，保留所有权利。如需使用，请联系仓库所有者。
