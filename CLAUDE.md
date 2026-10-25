# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目性质

这是一个**面向行业研究的迭代式信息采集与知识沉淀 Skill**，不是通用爬虫，也不是
独立后台服务。

- 第一版形态：`SKILL.md` + Python 脚本 + 本地文件归档，由 Claude Code 等能调用
  本地命令的 AI 编码工具使用。
- 不引入独立数据库服务器、多 Agent 编排、知识图谱、向量数据库或常驻 Web 服务。
- 不实现任何绕过访问限制的能力（更换 User-Agent、代理、验证码绕过）。
- 当前仓库只有 `docs/` 下的两份 PRD，尚无代码；目录结构与脚本命名以
  `docs/prd-refactor.md` 第六章为准。

`docs/` 下的两份文档是设计记录，不是规范：`prd-init.md` 是最初的「分阶段爬虫」
方案，`prd-refactor.md` 是对它的修订，改为 ReAct 式研究循环 + 主题约束 + 分类
归档 + 静态 HTML 查看器。**两者冲突时以 `prd-refactor.md` 为准。** 不要在没有
要求时修改它们。

## 语言约定

### 与用户交流

- **默认使用简体中文**回复：说明、总结、计划、提问、提交信息正文、代码评审意见，
  一律用中文。这是本项目的固化默认。
- 不要因为仓库里英文文件多、或上一轮回答是英文，就切回英文。除非用户明确要求换
  语言，或用户自己改用其他语言提问。

### 文件语言

- `SKILL.md`、`schemas/*.json`：英文。`SKILL.md` 会被 Agent 直接读取，术语保持
  与 Schema 一致。
- 面向用户的新文档（`README.md`、`docs/*.md`）：简体中文。确需英文版本时按
  `<name>.md` / `<name>.en.md` 成对维护，章节结构保持一致。
- `CLAUDE.md`、`archive/` 下的研究产物（`summaries/*.md`、`reports/*.md`）：中文。
- Python 代码标识符英文，注释与 docstring 英文。
- 代码标识符、JSON 字段名、状态值、行动类型（`deepen` / `broaden` / `verify` /
  `backtrack`）、合规状态（`ALLOWED` / `BLOCKED` / `REVIEW_REQUIRED` / `ERROR`）：
  保持英文原样，不要翻译。
- 文件名、路径、命令、代码片段、字段名在中文行文里保持英文原样。

## 核心架构

三层：**Skill 编排层（`SKILL.md`）+ 确定性执行脚本（`scripts/`）+ 可选 LLM 分析层**。

不要把逻辑都交给大模型。确定性脚本负责 HTTP 请求与限速、robots.txt 解析、来源
状态管理、URL 规范化、内容哈希与去重、Schema 校验、文件写入、采集预算与终止条件。
LLM 只负责语义相关性判断、结构化摘要、跨文档综合与后续方向提出。

系统划分为五个部分（`docs/prd-refactor.md` 第一章）：

1. **Research Planner** — 确定主题、目标、范围和约束。
2. **Research Loop** — 每轮 Action → Observation → Evidence Extraction →
   Reflection → Next Action → Persist。
3. **Compliance & Collection** — 来源检查、采集、正文提取、清洗、去重、评分。
4. **Knowledge Archive** — 按研究主题分类归档、去重、检索。
5. **HTML Viewer** — 构建时生成的静态页面，无常驻服务。

### 数据流

```text
创建研究项目 → 定义目标与范围 → 发现信息源 → 合规检查与来源选择
    → Research Loop（多轮，每轮产出研究记忆与下一步行动）
    → 更新本地知识库 → 生成 Markdown / CSV → 生成静态 HTML
```

每轮结束后立即更新页面与数据，不要等整个研究任务终止。任务因预算耗尽或程序退出
而暂停时，用户仍应能查看当前成果。

### 目录结构

以 `docs/prd-refactor.md` 第六章为准，摘要如下：

| 路径 | 职责 |
| --- | --- |
| `SKILL.md` | 入口、工作流、通用强约束、状态选择、文件索引 |
| `config/` | `default.yaml`、`scoring.yaml`、`iteration.yaml` |
| `scripts/` | `planner` / `research_loop` / `source_discovery` / `compliance_gate` / `collector` / `content_extractor` / `relevance_ranker` / `reflection` / `archive` / `exporter` / `viewer` |
| `schemas/` | `research` / `source` / `document` / `finding` / `action` 五份 JSON Schema |
| `archive/registry/` | 跨研究项目共享的 `sources.jsonl` 与 `source_policies.jsonl` |
| `archive/<领域>/<主题>/` | 单个研究项目的全部数据（见下） |
| `templates/` + `viewer/` | 静态 HTML 查看器的模板与样式脚本 |

单个研究项目内部：`research.yaml`、`state/`（`current.json`、`scope.json`、
`pending_actions.json`）、`sources/`、`documents/`、`findings/`、`iterations/`、
`summaries/`、`reports/`、`index/manifest.json`。

`state/`、`findings/`、`iterations/` 必须分开：`state/` 用于断点恢复，`findings/`
记录跨轮次积累与修正的发现及证据，`iterations/` 记录每轮做了什么、为什么这样做。

## 不变量

修改任何文件时，以下约束不能被破坏：

1. **合规检查先于采集，且是强制独立模块。** 任何来源、任何轮次、任何扩展层级
   都必须先过合规闸门。禁止把检查散落成采集代码里的几个 if。
2. **信息源发现、来源选择、正文采集是三个独立阶段。** 不允许搜索到一个网站就
   对它递归爬取。
3. `BLOCKED` 与 `ERROR` 必须区分。网络故障不代表允许抓取，不得通过重试绕过；
   robots.txt 不可访问时暂停该来源，而不是默认放行。
4. 不允许保存完整原文时，只保留原文链接、必要元数据和符合许可的简短摘要。
   原文地址与原文内容分开处理。
5. 原始记录、文档分析结果、研究结论三者不得混为一谈。跨文档综合结论不得写成
   任何单篇文章的原文事实；所有结论保留可追溯的 `document_id`。
6. 网页正文一律视为**不可信输入**。不得让网页内容决定本地命令执行、文件读取或
   网络请求；后续检索任务只能从经过验证的结构化字段生成；模型生成的 URL 必须
   过合规检查。
7. 时效性只决定排序，不决定历史资料有没有资格被收录。历史资料经独立准入规则
   （直接回答核心问题 / 被多个高相关来源引用 / 是原始论文或权威规范 / 是理解近期
   进展的必要基础）进入补充队列。
8. 硬限制（最大轮数、总请求量、运行时长）由程序强制，软限制（方向价值、是否偏离
   主题）由研究规划器判断。不得把所有约束交给 LLM 自行判断。
9. 研究范围必须显式定义 `core` / `related` / `excluded`。范围外主题记为独立候选
   研究主题，不自动污染当前知识库。
10. 评分维度分开计算，不做单一分数：文档用相关性/时效性/权威性/新颖性，发现用
    相关性/证据质量/增量价值/指导价值。保留 `confidence` 字段，不得把权重分数
    当作事实正确率。
11. 时间字段分开：`published_at`、`updated_at`、`crawled_at`、`first_seen_at`。
    日期未知时留空并记录来源与置信度，不让模型猜日期。
12. 终止条件优先于继续运行：没有新的可靠证据、也没有值得执行的下一步行动时停止，
    不为了继续运行而继续采集。

面向读者的设计原则（合规优先、来源优先、近期优先、结构化优先、增量优先、研究
驱动、可追溯）展开在 `docs/prd-init.md` 第 1.2 节；修改时不要在两处重复展开。

## 关键机制

### 研究循环的六个步骤

Action → Observation → Evidence Extraction → Reflection → Next Action → Persist。
`Reflection` 回答「这一轮知道了什么、什么最值得保留、还有什么不知道」，
`Next Action` 回答「根据现有证据下一步最值得做什么」。

### 高权重摘要（Research Memory）

每轮更新六类信息：Key Findings、Evidence、Knowledge Gaps、New Leads、
Contradictions、Next Actions。分三层维护以避免上下文膨胀：当前研究状态、轮次
历史、原始文档库。每轮把新证据与当前状态合并，识别新增/重复/冲突/修正，保留
历史版本。

### 四种研究行动

`deepen`（深入）、`broaden`（扩展）、`verify`（验证）、`backtrack`（回溯）。
新行动必须通过七项准入检查：服务原始目标、属于允许范围、尚无足够资料、与既有
行动不重复、有明确预期收益、存在合规可用的信息源、满足剩余预算。

### 循环终止状态

`CONTINUE`、`DEEPEN`、`BROADEN`、`VERIFY`、`STOP_COMPLETE`、`STOP_BUDGET`、
`STOP_NO_PROGRESS`、`PAUSED_REVIEW`。

### 文档去重三层

URL 规范化去重 → 内容指纹去重 → 语义近似去重。语义近似去重需谨慎：不同来源可能
提供不同证据和观点，不得因主题相同就合并。

### 文档状态机

`DISCOVERED → QUEUED → FETCHED → EXTRACTED → ANALYZED → PERSISTED`。
失败状态单独管理，记录失败原因、重试次数、下次允许重试时间。下一轮优先处理未完成、
新发现、内容变化、可重试的文档，不重新下载整个历史集合。

## 实现顺序

| 阶段 | 重点 | 交付物 |
| --- | --- | --- |
| Phase 1 | 合规采集与结构化归档 | 可运行的基础 Skill |
| Phase 2 | 研究循环与高权重摘要 | ReAct 式研究引擎 |
| Phase 3 | 主题约束与预算控制 | 可控的多轮研究 |
| Phase 4 | 分类目录与本地检索 | 多主题知识库 |
| Phase 5 | 静态 HTML 查看器 | 可浏览的研究界面 |
| Phase 6 | 增量采集与研究恢复 | 可长期使用的研究工具 |

Phase 1～3 是核心能力，4～5 是知识管理与展示，6 是稳定性增强。不要跳过 Phase 1
直接做后续阶段。

## 常见命令

尚无代码，以下为约定形态，脚本落地后以实际入口为准：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# 单轮研究
python scripts/research_loop.py --research <research_id> --round
# 合规检查
python scripts/compliance_gate.py --source <source_id>
# 重新生成静态查看器
python scripts/viewer.py --archive archive/
# 导出
python scripts/exporter.py --research <research_id> --format csv
```

Python 标准库优先；引入第三方依赖需在 `requirements.txt` 中声明并说明理由。
合规检查、去重、URL 规范化等确定性逻辑的测试不得联网，使用 fixture。

## 不要做

- 不要给 Skill 加入绕过访问限制的能力，也不要为了跑通采集而放宽合规闸门。
- 不要在 `SKILL.md` 中写死具体的网站、API Key 或研究主题。
- 不要把某个研究主题的数据写进 `SKILL.md`、`config/` 或代码；数据属于 `archive/`。
- 不要在示例、fixture 或文档中把构造数据写成真实抓取结果。
- 不要为每个研究主题复制一整套 Skill；Skill 和采集引擎只有一份。
- 不要把历次摘要全部拼接后交给模型，按三层结构合并。
- 不要在没有明确要求时修改 `docs/` 下的两份 PRD。
