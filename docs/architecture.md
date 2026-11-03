# 万象 · Wanxiang 架构设计

> **状态：目标架构。** 本文描述万象要达到的工程结构，不是当前代码的说明书。
> 当前实现范围、已实现与未实现项见第 12 章，以及 `README.md` 的「v1 范围与
> 未实现项」。
>
> 本文以 `docs/prd-new.md` 为产品意图来源，并继承 `docs/prd-refactor.md` 中
> 已经落地的合规与归档约束。三者冲突时：产品定位看 `prd-new.md`，工程结构看
> 本文，历史设计背景看两份旧 PRD。

## 一、文档定位与效力

| 文档 | 作用 | 效力 |
| --- | --- | --- |
| `docs/prd-new.md` | 产品愿景、能力边界、工作模式 | 产品意图来源 |
| `docs/architecture.md`（本文） | 工程架构、模块边界、目标目录、演进路线 | 工程结构以此为准 |
| `docs/prd-refactor.md` | ReAct 式研究循环、范围约束、分类归档的历史设计 | 本文未覆盖时参考 |
| `docs/prd-init.md` | 最初的分阶段采集方案 | 仅作历史背景 |
| `SKILL.md` | 宿主可直接执行的运行协议 | 运行行为以此为准 |
| `README.md` | 面向使用者的入口说明 | 安装与使用以此为准 |

本文不修改 `SKILL.md` 的运行契约。`SKILL.md` 中已有的命令、状态值、字段名与
合规语义继续有效；本文新增的模块只有在其接口被写入 `SKILL.md` 后才算正式进入
运行协议。

## 二、产品定位与边界

万象（Wanxiang）是一个面向开放信息环境的信息研究与知识发现 Skill。它围绕一个
研究目标，完成检索、调研、核验、分析、沉淀与追踪，产出有依据、可追溯、可持续
更新的研究成果。

### 2.1 六项核心能力

| 能力 | 解决的问题 | 主要产出 |
| --- | --- | --- |
| 信息检索 Information Retrieval | 找到信息 | 候选来源、候选文档、检索结果 |
| 专题调研 Deep Research | 理解一个主题 | 子问题、研究记忆、研究报告 |
| 信息核验 Verification | 判断信息是否可信 | 证据、冲突记录、可信度标记、核验队列 |
| 信息分析 Analysis | 提取观点、规律与关系 | 发现、对比表、时间线、实体关系 |
| 知识沉淀 Curation | 让成果可复用 | 分类归档、JSONL、CSV、Markdown、SQLite 索引 |
| 持续追踪 Monitoring | 发现长期变化 | 快照、变化摘要、研究建议 |

### 2.2 四种工作模式

四种模式是同一个 Skill 的不同任务入口，不是四个独立 Skill。

| 模式 | 入口命令 | 目标 | 是否建立完整研究档案 |
| --- | --- | --- | --- |
| 快速检索 Quick | `/wanxiang-quick` | 快速回答一个边界明确的问题 | 否，可选轻量记录 |
| 专题调研 Deep Research | `/wanxiang-deep` | 系统研究一个主题 | 是 |
| 对比分析 Comparison | `/wanxiang-comparison` | 对多个对象做统一维度比较 | 是，额外产出结构化对比记录 |
| 持续追踪 Monitoring | `/wanxiang-monitor` | 关注一个主题的长期变化 | 是，在既有项目上增量更新 |

### 2.3 明确不做

- 不是通用爬虫，不对一个网站做无限递归抓取。
- 不是搜索引擎，不提供常驻检索服务。
- 不是常驻 Web 服务，查看器是构建时生成的静态页面。
- 不引入向量数据库、知识图谱、多 Agent 编排或独立数据库服务器。
- 不实现任何绕过访问限制的能力：不更换 User-Agent、不使用代理、不绕验证码、
  不通过重试或延迟获得不同结果。
- 不把网页正文当作指令，也不让网页内容决定本地命令、文件读取或网络请求。

### 2.4 与其他 Skill 的分工

| Skill | 职责 | 产出 |
| --- | --- | --- |
| 万象 Wanxiang | 获取、研究、核验信息 | 研究报告、资料库 |
| 格物 Gewu | 学习、理解、拆解知识 | 学习笔记、原理图解 |
| 观象 Guanxiang | 天气数据解读与辅助决策 | 天气判断、决策依据 |
| 天工 Tiangong | 写作、内容组织与表达 | 文章、文档、图文内容 |

## 三、架构总览

### 3.1 分层

| 层 | 职责 | 实现 |
| --- | --- | --- |
| Skill 编排层 | 理解用户意图、选择模式、编排流程、做语义判断 | `SKILL.md` + LLM 宿主 |
| 检索与采集层 | 多源检索、来源发现、合规检查、网页/API/RSS 采集、正文提取、去重 | `scripts/` 中的确定性模块 |
| 分析与核验层 | 提取主张与证据、识别冲突、核验、形成发现 | LLM 宿主 + 结构化中间产物 |
| 研究控制层 | 任务拆解、行动准入、预算、终止判断、状态持久化 | `scripts/` + LLM 宿主 |
| 归档与索引层 | 分类归档、去重、跨项目索引、导出 | JSONL / Markdown / CSV / SQLite |
| 展示层 | 研究总览、专题详情、资料浏览、对比与变化查看 | 静态 HTML |

### 3.2 六个子系统

1. **Research Planner** — 确定研究主题、目标、子问题、范围与预算。
2. **Research Loop** — 执行 Action → Observation → Evidence → Reflection →
   Next Action → Persist。
3. **Compliance & Collection** — 来源检查、采集、正文提取、清洗、去重、评分。
4. **Analysis & Verification** — 语义分析、事实核验、冲突识别、对比与时间线。
5. **Knowledge Archive & Index** — 按研究项目归档，建立跨项目派生索引。
6. **Viewer** — 生成单项目与工作区级静态页面。

### 3.3 数据流

```text
用户提出研究任务
    → 选择工作模式（quick / deep / comparison / monitor）
    → Planner：明确目标、子问题、core / related / excluded、预算
    → Search：多源检索，产出候选结果
    → Source Discovery / Selection：候选 → 用户确认来源
    → Compliance Gate：逐来源合规检查（强制，不可绕过）
    → Research Loop（多轮）
        Action → Observation（采集）→ Evidence（分析 / 核验）
        → Reflection（研究记忆）→ Next Action → Persist
    → Archive & Index：JSONL / Markdown / CSV / SQLite
    → Viewer：单项目页面 + 工作区总览
```

### 3.4 确定性边界

不是所有环节都调用大模型。

| 环节 | 执行者 | 原因 |
| --- | --- | --- |
| HTTP 请求、限速、超时、重试上限 | 脚本 | 可复现、可审计 |
| robots.txt 解析、条款检查、访问控制判断 | 脚本 + 人工授权 | 合规必须由程序强制 |
| URL 规范化、内容哈希、去重 | 脚本 | 确定性逻辑 |
| Schema 校验、文件写入、预算与终止条件 | 脚本 | 不能让模型自行放宽 |
| 相关性判断、摘要、矛盾识别、研究反思 | LLM 宿主 | 语义工作 |
| 关键事实追溯、跨来源核验 | LLM 宿主 + 脚本记录 | 结论必须可追溯到 `document_id` |

硬限制（最大轮数、总请求量、运行时长、单轮文档数）由程序强制；软限制（方向
价值、是否偏离主题）由研究规划器判断。不得把所有约束交给 LLM 自行判断。

## 四、工作模式

### 4.1 快速检索 Quick

```text
问题理解 → 检索 → 筛选 → 信息核验 → 答案输出
```

- 优先权威来源，关键结论附原始链接。
- 不强制建立完整研究档案；需要时可写入轻量记录。
- 控制检索深度与执行成本。
- 仍然遵守合规闸门：未通过检查的来源不进入采集。

### 4.2 专题调研 Deep Research

1. 明确研究目标与范围。
2. 将主题拆分为子问题，制定检索策略。
3. 发现候选来源，识别官方、学术、开源、社区、媒体、数据来源。
4. 逐来源做合规检查。
5. 近期资料优先采集，再围绕引用关系与知识缺口补充历史资料。
6. 去重、提取证据、识别冲突，形成阶段性结论。
7. 反思本轮，决定深入、扩展、核验或停止。
8. 输出报告、结构化数据、来源清单与静态 HTML。

### 4.3 对比分析 Comparison

对比分析不是罗列功能，而是围绕**统一维度**收集证据。典型维度包括：

- 定位与设计理念
- 架构与核心机制
- 能力边界与扩展方式
- 兼容性与集成
- 部署与运维成本
- 社区活跃度与维护状态
- 适用场景与局限

产出为结构化对比记录（`comparisons/`）与对比表；每个单元格保留证据
`document_id`。维度在规划阶段确认，后续轮次不得随意增删而不记录原因。

### 4.4 持续追踪 Monitoring

- 在既有研究项目上执行增量检索。
- 与上一次快照比较，识别新增文档、新增/修正发现、失效结论与新知识缺口。
- 只关注新增信息，不重复下载历史集合。
- 输出变化摘要与下一步研究建议。
- 沿用同一套合规与预算约束。

### 4.5 模式与产物

| 模式 | 入口命令 | 规划记录 | 采集 | 归档 | 主要产物 | v1 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| Quick | `/wanxiang-quick` | 可选 | 窄范围 | 可选 | 答案 + 引用 | 未实现（入口只提示并引导 deep） |
| Deep Research | `/wanxiang-deep` | `research.yaml` + `state/scope.json` | 多轮 | 完整 | 报告、发现、HTML | 已实现 |
| Comparison | `/wanxiang-comparison` | 维度写入 `research.yaml` | 多源 | 完整 + `comparisons/` | 对比表、差异分析 | 未实现（入口只提示并引导 deep） |
| Monitoring | `/wanxiang-monitor` | 追踪配置 | 增量 | 快照 + 变化 | 变化摘要、建议 | 未实现（入口只提示并引导 deep） |

## 五、研究引擎

### 5.1 一轮的六个步骤

| 步骤 | 执行者 | 交接物 |
| --- | --- | --- |
| Action | 宿主 | `actions.json`，符合 `schemas/action.schema.json` |
| Observation | 脚本 | stdout 上的 round handoff，含新文档与剩余预算 |
| Evidence | 宿主 | findings，每条带 `document_id` 证据 |
| Reflection | 宿主 | `observations`：学到了什么、缺什么、什么是新的 |
| Next Action | 宿主 | `next_actions`，供下一轮 Action 复用 |
| Persist | 脚本 | 写入 iteration、findings、state、summary、manifest |

脚本不调用任何模型，不读取 API Key；语义判断全部由宿主完成。

### 5.2 研究记忆

每轮更新六类信息：

- Key Findings
- Evidence
- Knowledge Gaps
- New Leads
- Contradictions
- Next Actions

分三层维护，避免上下文膨胀：

1. **当前研究状态** — `state/current.json` 与 `summaries/current.md`。
2. **轮次历史** — `iterations/round-NNN.json`。
3. **原始文档库** — `documents/documents.jsonl` 与允许保留的正文。

每轮把新证据合并进当前状态，识别新增、重复、冲突与修正，保留历史版本，而不是
把历次摘要拼接后交给模型。

### 5.3 四种行动与准入

行动类型：`deepen`、`broaden`、`verify`、`backtrack`。

每个新行动必须通过七项准入检查：

1. 服务原始研究目标。
2. 属于允许的研究范围。
3. 尚无足够资料。
4. 与既有行动不重复。
5. 有明确的预期收益。
6. 存在合规可用的信息源。
7. 满足剩余预算。

范围外的主题记为独立候选研究主题或候选线索，不自动并入当前项目。

### 5.4 终止状态

`CONTINUE`、`DEEPEN`、`BROADEN`、`VERIFY`、`STOP_COMPLETE`、`STOP_BUDGET`、
`STOP_NO_PROGRESS`、`PAUSED_REVIEW`。

终止条件优先于继续运行：没有新的可靠证据，也没有值得执行的下一步行动时停止，
不为了继续运行而继续采集。

### 5.5 范围与预算

- `core`：核心研究范围，由用户明确指定。
- `related`：允许扩展，但每个新方向必须解释与核心目标的关系。
- `excluded`：不允许自动扩展。

预算分为硬限制与软限制：

| 类型 | 项目 | 执行者 |
| --- | --- | --- |
| 硬限制 | 最大轮数、单轮文档数、单项目文档总数、总请求量、运行时长 | 程序 |
| 软限制 | 方向价值、主题相关性、优先级门槛 | 研究规划器 |

### 5.6 重要性与可信度

必须区分结论的**重要性**与**可信度**：

- 重要但证据不足的结论进入核验队列，而不是直接成为研究事实。
- `confidence` 表示证据支持程度，不是事实正确率。
- 不得把权重分数当作事实正确率，也不得把单一综合分当作结论质量。

## 六、检索与信息源

### 6.1 信息源分类

| 类型 | 典型来源 | 主要用途 |
| --- | --- | --- |
| 官方来源 | 官方网站、官方博客、公告 | 权威事实 |
| 学术来源 | arXiv、论文库、学术期刊 | 理论与技术研究 |
| 开源来源 | GitHub、GitLab | 项目与代码分析 |
| 技术社区 | Hacker News、技术论坛 | 实践经验与社区反馈 |
| 媒体来源 | 行业媒体、新闻网站 | 动态与趋势 |
| 数据来源 | 开放数据集、政府统计 | 数据分析 |

### 6.2 三个独立阶段

信息源发现、来源选择、正文采集是三个独立阶段。不允许搜索到一个网站就对它
递归爬取。

```text
Search（检索候选）
    → Discover（整理为候选来源，标注来源类型与可信状态）
    → Select（用户确认哪些来源可读）
    → Compliance Gate（逐来源检查）
    → Collect（只采集 ALLOWED 的来源）
```

### 6.3 统一检索接口

目标新增 `scripts/search.py`，作为多源检索的统一入口。它只负责检索与结果规范化，
不负责合规判断，也不直接采集。

职责：

- 接收查询、时间范围、语言、来源类型偏好。
- 调用一个或多个 provider，或接收宿主提供的检索结果。
- 将结果规范化为候选记录：标题、URL、摘要、发布时间、来源类型、provider。
- 去重、分类、排序；不把排序结果当作可信度。
- 输出候选结果，交给 source discovery 与用户确认。

provider 类型（目标）：

| provider | 说明 |
| --- | --- |
| `manual` | 用户直接给出 URL |
| `llm` | 宿主从模型知识生成候选，全部标记未验证 |
| `web_api` | 第三方网页搜索 API |
| `official_api` | 官方 API（如代码托管、论文库） |
| `rss` | RSS / Atom feed |
| `academic_api` | 学术检索接口 |

约束：

- 脚本不内置 API Key，不把密钥写入仓库；密钥由宿主环境提供。
- 未知 provider 名报错，不静默回退。
- 搜索结果不是来源，更不是许可；任何 URL 都必须经过用户确认与合规检查。

### 6.4 三层数据对象

| 对象 | 含义 | 是否经过合规 | 是否可采集 |
| --- | --- | --- | --- |
| 检索结果 result | 一次检索返回的条目 | 否 | 否 |
| 候选来源 candidate | 被整理为来源候选的地址 | 否 | 否 |
| 已确认来源 source | 用户确认、`discovery_verified: true` | 是 | 仅 `ALLOWED` |

## 七、合规机制

### 7.1 独立闸门

`scripts/compliance_gate.py` 是强制独立模块。任何来源、任何轮次、任何扩展层级
都必须先过合规闸门。禁止把检查散落成采集代码里的几个 `if`。

检查内容：

1. robots.txt。
2. 网站服务条款。
3. API / RSS 的访问规则与频率限制。
4. 登录、授权与访问控制要求。
5. 允许保存和使用的内容范围。
6. 检查时间、规则依据与结果记录。

### 7.2 状态语义

| 状态 | 含义 |
| --- | --- |
| `ALLOWED` | 本工具执行的检查未发现禁止采集的规则；不构成授权或许可 |
| `BLOCKED` | 规则明确禁止，或来源拒绝可达性请求（401/403 或其他 4xx） |
| `REVIEW_REQUIRED` | 需要人工判断；只能由带范围与有效期的显式授权解除 |
| `ERROR` | 检查无法完成（例如 robots.txt 读不到）；来源暂停，不等于允许 |
| `UNREACHABLE` | 超时、网络故障或 5xx；来源暂停，不等于允许 |
| `DISCOVERED` / `CHECKING` | 尚未作出决定 |

`BLOCKED` 与 `ERROR` 必须区分。网络故障不代表允许抓取，不得通过重试绕过；
robots.txt 不可访问时暂停该来源，而不是默认放行。

### 7.3 人工授权

`REVIEW_REQUIRED` 不会自动变成 `ALLOWED`。用户显式授权时，通过 `save_policy`
写入 `archive/registry/source_policies.jsonl`，记录授权范围（路径与访问方式）与
`expires_at`。`BLOCKED`、`ERROR`、`UNREACHABLE` 不能被授权覆盖。授权是产品
动作，不是法律许可。

### 7.4 不可绕过

- 不更换 User-Agent，不使用代理，不绕验证码。
- 不通过重试、延迟或更换地址获得不同结果。
- 合规决定绑定被检查的域名与地址；超过 `compliance_recheck_days` 后重新检查。
- `collector.assert_compliance_allows` 是结构性兜底，只有 `ALLOWED` 能通过。

### 7.5 优先公开授权渠道

默认优先使用官方 API、RSS 与公开授权数据源。robots.txt 不等于授权许可，
搜索引擎可以检索到的内容也不代表可以任意抓取和存储。

## 八、分析与核验

### 8.1 分析层 Analyzer

目标新增 `scripts/analyzer.py` 的接口约定（实现由宿主与脚本共同完成）。

输入：本轮 `document_texts`（不可信内容）、文档元数据、当前研究状态。

输出（全部结构化，全部带 `document_id`）：

- 关键主张（claim）与关键事实。
- 实体：人物、组织、产品、技术。
- 对比维度与每个维度的证据。
- 时间线事件。
- 冲突与不一致。
- 候选发现（finding），带证据类型：`supports`、`contradicts`、
  `partially_supports`。

约束：

- 网页正文视为不可信输入；分析结果不得触发命令、文件读取或网络请求。
- 跨文档综合结论不得写成任何单篇文章的原文事实。
- 只允许保存符合许可的简短摘录；不得持久化 `document_texts`。

### 8.2 核验层 Verifier

目标新增 `scripts/verifier.py`。

核验队列来源：

- 重要但 `confidence` 低的发现。
- 多个来源互相矛盾的结论。
- 时效性关键、但只有单一来源支持的事实。

核验动作：

1. 追溯原始来源与发布时间。
2. 区分事实、观点、推测与宣传内容。
3. 对比多个来源的描述与结论。
4. 识别过时信息、证据不足与来源依赖。
5. 更新 `confidence`、记录冲突与核验结论。

核验不能把「多个来源重复同一说法」直接当作事实；转载与引用必须追溯到原始来源。
核验也不改变合规状态：需要新来源时，仍然重新走发现、确认与合规检查。

### 8.3 分析与核验的边界

- 分析回答「这份材料说了什么、意味着什么」。
- 核验回答「这个说法是否可信、证据是否足够」。
- 反思回答「这一轮知道了什么、下一步最值得做什么」。
- 三者不得混为一谈；原始记录、分析结果、研究结论分别存储。

## 九、知识归档与索引

### 9.1 存储格式

| 数据 | 格式 | 说明 |
| --- | --- | --- |
| 研究配置 | YAML | `research.yaml`，便于人工编辑 |
| 来源登记 | JSONL | 追加式，便于迁移 |
| 文档元数据 | JSONL | 主存储 |
| 文档表格视图 | CSV | 人工查看与交换 |
| 原文内容 | 独立文件（仅在允许时） | 默认不保存全文 |
| 研究状态 | JSON | 断点恢复 |
| 轮次历史 | JSON | 每轮一份 |
| 大量文档检索 | SQLite | 派生索引，可重建 |
| 报告 | Markdown / HTML | 阅读与分享 |
| 浏览界面 | 静态 HTML | 无服务端 |

### 9.2 归档总原则

- 按研究主题分类归档：`archive/<domain>/<topic>/`。
- 一个研究项目一个目录；不要为每个主题复制一整套 Skill。
- 来源注册表可跨项目共享。
- 文档可以同时关联多个研究项目，但原始内容不重复保存。
- 文档相关性按研究项目分别计算。

### 9.3 单个研究项目布局

```text
archive/<domain>/<topic>/
├── research.yaml
├── state/
│   ├── current.json
│   ├── scope.json
│   └── pending_actions.json
├── sources/
│   └── selected_sources.jsonl
├── documents/
│   ├── documents.jsonl
│   ├── documents.csv
│   └── content/                  # 仅在允许保存时存在
├── findings/
│   ├── findings.jsonl
│   └── evidence.jsonl
├── comparisons/                  # 目标：对比分析记录
├── iterations/
│   ├── round-001.json
│   └── ...
├── summaries/
│   ├── current.md
│   └── snapshots/
├── reports/
│   └── research-report.md
├── tracking/                     # 目标：持续追踪
│   ├── snapshots/
│   └── changes/
├── inputs/                       # 宿主每轮输入文件（可选）
├── index/
│   └── manifest.json
└── viewer/                       # 生成物，不是记录
```

`state/`、`findings/`、`iterations/` 必须分开：

- `state/` 用于断点恢复。
- `findings/` 记录跨轮次积累与修正的发现及证据。
- `iterations/` 记录每轮做了什么、为什么这样做。

### 9.4 跨项目复用

稳定 ID：

```text
research_id: res_xxxxxx
source_id:   src_xxxxxx
document_id: doc_xxxxxx
finding_id:  find_xxxxxx
```

- `archive/registry/sources.jsonl` 作为目标跨项目来源注册表。
- 同一来源可被多个项目引用。
- 同一文档可关联多个项目；项目内保存关联、评分与研究上下文，不复制原文。
- 文档相关性、时效性、权威性、新颖性按项目分别评分。

### 9.5 SQLite 派生索引

目标新增 `scripts/indexer.py`，生成 `archive/index/wanxiang.sqlite3`。

原则：

- **SQLite 是派生索引，不是事实来源。** 删除它可以由 JSONL 重建。
- 任何写操作先落 JSONL / Markdown，再更新索引；索引写入失败不破坏归档。
- 不存完整原文，只索引元数据、已保存摘要与允许的摘录。
- 支持跨项目查询、按时间/来源/相关性筛选、全文检索（FTS5，范围限于已保存
  文本）。

建议表结构：

| 表 | 内容 |
| --- | --- |
| `research` | 研究项目、目标、状态、范围 |
| `source` | 来源、类型、合规状态、检查时间 |
| `document` | 文档元数据、指纹、时间字段、保留模式 |
| `research_document` | 项目与文档的关联及项目内评分 |
| `finding` | 发现、置信度、重要性、状态 |
| `evidence` | 发现与文档的证据关系 |
| `iteration` | 轮次、行动、决策、预算消耗 |
| `comparison` | 对比维度、对象与证据 |
| `snapshot` | 追踪快照与变化 |

### 9.6 保留模式与原文

- `reference`：只保留链接、必要元数据与内容指纹（默认）。
- `excerpt`：保留符合许可的简短摘录。
- `full`：仅在许可明确允许且已记录依据时保存全文。

原文地址与原文内容分开处理。无法保存全文时，只保留链接、元数据和合规摘录。

## 十、查看器

### 10.1 单项目查看器

由 `scripts/viewer.py` 生成到 `<project>/viewer/`，包含：

1. 研究总览：摘要、核心结论、证据、知识缺口、迭代历史。
2. 资料列表：按关键词、来源、时间、类型、相关性筛选。
3. 资料详情：原始来源、摘要、关键内容、关联结论、原文链接。
4. 来源与合规状态。
5. 迭代历史。

### 10.2 工作区查看器

目标新增工作区级查看器，生成到 `archive/viewer/`，包含：

- 研究总览：专题数量、来源数量、文档数量、最近更新时间。
- 跨专题检索与筛选。
- 对比分析视图。
- 持续追踪的变化时间线。

`prd-new.md` 把工作区查看器放在仓库根目录 `viewer/`。本文调整为
`archive/viewer/`：查看器是运行数据，不是 Skill 源码；放在被 `.gitignore`
忽略的 `archive/` 下，避免生成物与脚本、模板混在一起。

### 10.3 静态约束

- 不引入前后端服务，不引入 React / Vue。
- 页面只读取归档，不联网、不改写记录。
- 可以随时重建；重建不产生新的研究事实。
- 文档正文未保存时，只展示元数据、已保存摘要与原文链接，绝不虚构全文。

## 十一、目标目录结构

标记说明：`现有` 表示 v1 已存在；`目标` 表示本架构新增或调整。

```text
research-scout-skill/                 # 仓库名本轮不变；产品名 万象 / Wanxiang
├── SKILL.md                          # 现有：宿主运行协议
├── CLAUDE.md                         # 现有：仓库开发约定
├── README.md                         # 现有：中文入口
├── README.en.md                      # 现有：英文入口
├── requirements.txt                  # 现有
├── config/
│   ├── default.yaml                  # 现有
│   ├── scoring.yaml                  # 现有
│   └── iteration.yaml                # 现有
├── scripts/
│   ├── research_loop.py              # 现有：唯一 CLI 入口
│   ├── planner.py                    # 现有
│   ├── search.py                     # 目标：统一检索入口
│   ├── source_discovery.py           # 现有
│   ├── compliance_gate.py            # 现有：强制独立合规闸门
│   ├── collector.py                  # 现有
│   ├── content_extractor.py          # 现有
│   ├── analyzer.py                   # 目标：语义分析接口
│   ├── verifier.py                   # 目标：核验
│   ├── deduplicator.py               # 现有
│   ├── action_gate.py                # 现有
│   ├── reflection.py                 # 现有
│   ├── tracker.py                    # 目标：持续追踪
│   ├── archive.py                    # 现有
│   ├── indexer.py                    # 目标：SQLite 派生索引
│   ├── exporter.py                   # 现有
│   ├── viewer.py                     # 现有
│   └── lib/                          # 现有
├── schemas/
│   ├── research.schema.json          # 现有
│   ├── source.schema.json            # 现有
│   ├── document.schema.json          # 现有
│   ├── finding.schema.json           # 现有
│   ├── action.schema.json            # 现有
│   ├── comparison.schema.json        # 目标
│   └── tracking.schema.json          # 目标
├── templates/                        # 现有：查看器共用样式与脚本
│   ├── styles.css
│   └── app.js
├── archive/                          # 运行数据，git 忽略
│   ├── registry/
│   │   ├── sources.jsonl             # 目标：跨项目来源注册表
│   │   └── source_policies.jsonl     # 现有：人工授权记录
│   ├── index/
│   │   └── wanxiang.sqlite3          # 目标：派生索引
│   ├── viewer/                       # 目标：工作区查看器
│   └── <domain>/<topic>/             # 现有：单个研究项目
│       ├── research.yaml
│       ├── state/
│       ├── sources/
│       ├── documents/
│       ├── findings/
│       ├── comparisons/              # 目标
│       ├── iterations/
│       ├── summaries/
│       ├── reports/
│       ├── tracking/                 # 目标
│       ├── inputs/
│       ├── index/
│       └── viewer/
└── tests/                            # 现有
```

### 11.1 与 `prd-new.md` 目录建议的差异

| `prd-new.md` 建议 | 本文决定 | 理由 |
| --- | --- | --- |
| `scripts/compliance.py` | 保留 `compliance_gate.py` | 现有名称更明确，且已写入 `SKILL.md` 与测试 |
| `scripts/archive.py`、`viewer.py` 等平铺 | 保留现有模块拆分，新增 `search.py`、`analyzer.py`、`verifier.py`、`tracker.py`、`indexer.py` | 不重命名已落地的模块，减少无收益迁移 |
| 根目录 `viewer/` | `archive/viewer/` | 生成物属于运行数据，不属于源码 |
| 仅四份 Schema | 增加 `comparison.schema.json`、`tracking.schema.json` | 对比与追踪需要结构化记录，不能只写进自由文本 |
| `scripts/` 每项一个 CLI | 继续由 `research_loop.py` 作为唯一 CLI，其余为模块函数或子命令 | 保持单一入口，宿主调用方式一致 |

## 十二、当前实现与目标差距

### 12.1 已实现（v1）

- 合规采集与来源登记：robots.txt、可达性、条款页触发 `REVIEW_REQUIRED`、人工
  授权记录。
- 分类归档：`archive/<domain>/<topic>/`，`state` / `findings` / `iterations`
  分离。
- 研究循环：行动准入、范围约束、预算、去重、发现合并、研究记忆、终止状态。
- 导出：Markdown 报告与 CSV。
- 静态 HTML 单项目查看器。
- 不可信内容隔离：`document_texts` 仅本轮使用、不持久化；提示注入警告仅提示。
- 断点恢复限于状态与归档文件：`state/` 与归档可恢复，但没有自动跨轮续跑。

### 12.2 目标新增

| 能力 | 目标模块 | v1 状态 |
| --- | --- | --- |
| 统一多源检索 | `scripts/search.py` + provider 接口 | 未实现；只有 `manual` / `llm` 候选发现 |
| 语义分析 | `scripts/analyzer.py` | 未实现；由宿主直接产出 findings |
| 事实核验 | `scripts/verifier.py` + 核验队列 | 未实现 |
| 对比分析 | `comparison.schema.json` + `comparisons/` | 未实现 |
| 持续追踪 | `scripts/tracker.py` + `tracking/` | 未实现 |
| 跨项目来源注册表 | `archive/registry/sources.jsonl` | 未实现；来源登记在项目内 |
| SQLite 索引 | `scripts/indexer.py` | 未实现 |
| 工作区查看器 | `archive/viewer/` | 未实现；只有单项目查看器 |
| 自动重试与下一轮优先 | `collector.py` + 文档状态机 | 未实现；5xx/429 当轮失败，robots 失败永不重试 |
| 文档相关性自动评分 | 评分模块 | 未实现；Schema 字段存在但不填 |
| API / Feed 条款与额度 | `compliance_gate.py` | 未实现；未声明 terms 的 rss/api 只按 robots.txt 评估 |

### 12.3 已声明但未消费的配置

`config/default.yaml` 的 `max_attempts`，以及 `config/iteration.yaml` 的
`max_branch_depth`、`max_active_branches`、`min_new_information_ratio`、
`stop_when_no_progress_rounds`，在 v1 中已定义但没有脚本读取或执行。架构文档
保留这些配置作为目标契约，不代表它们已经生效。

## 十三、演进路线

合规闸门在每一个阶段都保持强制，不因新增能力而放宽。

| 阶段 | 重点 | 主要交付 | 对应 `prd-new.md` |
| --- | --- | --- | --- |
| P0 | 文档与契约收敛 | 本文、README、目录约定 | 设计阶段 |
| P1 | 统一检索入口 | `search.py`、provider 接口、结果规范化 | 阶段一：基础检索 |
| P2 | 语义分析与对比 | `analyzer.py`、`comparison.schema.json`、`comparisons/` | 阶段二：专题调研 |
| P3 | 核验 | `verifier.py`、核验队列、冲突记录 | 阶段二：专题调研 |
| P4 | 持续追踪 | `tracker.py`、`tracking/` 快照与变化 | 阶段三：迭代研究引擎 |
| P5 | 索引与工作区查看器 | `indexer.py`、`archive/viewer/`、SQLite FTS | 阶段四：知识沉淀与展示 |
| P6 | 稳定性增强 | 有限重试、跨轮续跑、增量采集、评分落地 | 阶段三/四 |

顺序原则：

1. 先补齐检索入口，否则四种模式都缺少共同的起点。
2. 分析与核验先于对比与追踪，因为后两者依赖结构化证据。
3. SQLite 与工作区查看器放在有足够多项目之后，避免过早引入派生层复杂度。
4. 稳定性增强最后做，但不能以「先跑通」为由放宽合规。

## 十四、不变量

修改任何文件时，以下约束不能被破坏：

1. **合规检查先于采集，且是强制独立模块。** 任何来源、任何轮次、任何扩展层级
   都必须先过合规闸门。禁止把检查散落成采集代码里的几个 `if`。
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
   进入补充队列。
8. 硬限制由程序强制，软限制由研究规划器判断。不得把所有约束交给 LLM 自行判断。
9. 研究范围必须显式定义 `core` / `related` / `excluded`。范围外主题记为独立候选
   研究主题，不自动污染当前知识库。
10. 评分维度分开计算，不做单一分数。保留 `confidence` 字段，不得把权重分数当作
    事实正确率。
11. 时间字段分开：`published_at`、`updated_at`、`crawled_at`、`first_seen_at`。
    日期未知时留空并记录来源与置信度，不让模型猜日期。
12. 终止条件优先于继续运行：没有新的可靠证据、也没有值得执行的下一步行动时停止。
13. **SQLite 是派生索引。** 它可以重建，不是事实来源；写索引失败不破坏归档。
14. **核验不改变合规状态。** 核验需要新来源时，仍然重新走发现、确认与合规检查。

## 十五、待决问题

以下问题不影响当前文档结构，但在对应阶段开始前需要确认：

1. **产品与 Skill 改名时机（已决策）。** 对外入口已迁移为 `/wanxiang-quick`、
   `/wanxiang-deep`、`/wanxiang-comparison`、`/wanxiang-monitor` 四个 Skill
   命令；v1 只有 `/wanxiang-deep` 完整可用，其余三个入口只声明未实现并引导到
   deep。仓库名仍为 `research-scout`，合规传输标识 `ResearchScout/0.1` 有意
   保留：它已绑定 robots.txt 分组与既有合规决策，改名会使历史决策失效。
2. **搜索 provider 的优先级。** 先接官方 API / RSS，还是先接第三方网页搜索
   API；前者合规成本低，后者覆盖面广。
3. **SQLite FTS 的索引范围。** 是否索引已保存摘要与摘录；默认不索引任何未保存
   的正文。
4. **全文保留策略。** `full` 模式只在许可明确允许时启用；需要定义「许可明确」
   的记录字段与审核流程。
5. **多项目共享文档的去重粒度。** 同一 URL 在不同项目中的内容变化，是否需要
   独立版本记录。

## 十六、术语表

| 术语 | 含义 |
| --- | --- |
| 研究项目 | `archive/<domain>/<topic>/` 下的一个完整研究单元 |
| 轮次 round | 研究循环的一次完整执行 |
| 行动 action | 本轮提出的研究方向：`deepen` / `broaden` / `verify` / `backtrack` |
| 发现 finding | 跨文档综合后的结论，带证据 `document_id` |
| 证据 evidence | 文档对发现的支持、反对或部分支持关系 |
| 候选来源 candidate | 尚未确认或未通过合规检查的来源 |
| 合规闸门 Compliance Gate | 独立的来源检查模块 |
| 研究记忆 Research Memory | 当前状态中的关键发现、证据、缺口、线索、冲突与下一步 |
| 派生索引 | 由归档文件重建的 SQLite 查询索引，不是事实来源 |
