# MODOU × OuSheng 偏差分析集成方案

**版本：Final 1.0（开发基线，设计冻结）**
**定位：OuSheng 为主运行，MODOU 作为独立 Assurance Runtime，对绳上任务与 design 级交付做偏差分析**
**状态：进入 P0 开发。所有能力断言按 Implemented / Experimental / Planned 标注，禁止把设计写成现状**
**背景体系：MOSS·OuSheng·MODOU 三 Runtime 统一体系（Final 1.0）**

---

## 1. 目标与硬约束

在不侵入 OuSheng 生产系统的前提下，使 MODOU 能够：固定 board 评估快照 → 按分级路由筛选分析对象 → 对 agent 任务级与 design 级两层执行独立偏差分析 → 产出可追溯 Assessment →（校准达标后）经官方接口提交纠偏建议 → 对纠偏结果独立复验。

**四条硬约束（全程有效）：**

1. **高内聚低耦合**：两系统各自可单独运行，也可合并运行；合并不得要求任何一方为另一方改代码。
2. **两级分析**：design 拆分为多 agent 任务，MODOU 分别分析每个 agent 任务的完成（item 级）与 design 整体的完成（design 级）。
3. **任务分级**：分析入口分级，低价值任务不进 MODOU，高风险任务必须进。
4. **绳牛纪律 + 质量第一**：MODOU 只做方向性任务（事实案卷、分析协议、质量门），语义判断全部交给大模型；**偏差分析不好，不如不分析**——写回以校准达标为唯一前置条件。

---

## 2. 职责边界

| Runtime | 权威事实 | 核心职责 | 越权禁止 |
|---|---|---|---|
| MOSS（缺位，未来接入） | 决策事实与版本 | 目标、验收标准、决策反馈 | 不组织执行 |
| OuSheng（生产运行） | 任务组织、执行状态、执行证据 | WorkItem / Actor / Contract / 证据提交 | 不擅自改决策标准 |
| MODOU | Assessment / Finding / Verification / Reassessment | 独立评估、偏差诊断、纠偏建议、复验 | 不接管决策权与组织执行权 |

三种完成状态不得合并：`Execution Completed`（执行方报告完成）≠ `Deliverable Accepted`（交付过验收）≠ `Decision Outcome Achieved`（业务目标达成）。OuSheng 的 `done` 仅是第一种。

**MODOU 永久禁止**：修改既有 WorkItem 状态/负责人/验收要求；自动分配；自动授权；绕过官方接口手改 board YAML；将被评估方声明当事实。

---

## 3. 耦合模型与运行形态

### 3.1 耦合面（全部耦合，仅此三项）

| 耦合面 | 方向 | 性质 |
|---|---|---|
| board git 快照（`.ousheng/*.yaml` + `designs/`，经 `git show <rev>:` 读） | OuSheng → MODOU | 数据耦合，只读 |
| OuSheng 官方 MCP 工具（`create_work_item` / `add_evidence`） | MODOU → OuSheng | 接口耦合，仅 P3 写回 |
| CLI 退出码（0/1/2，评估结论与运行错误分离） | MODOU → 可选调用方 | 约定耦合，cruise 非阻塞观察用 |

之外零耦合：不共享库/数据库，不嵌入进程，不要求 OuSheng 加字段、加接口、改一行代码。内聚判据：`asr/assurance/` 核心包不含任何 OuSheng 词汇；`asr/adapters/ousheng/` 是唯一知道 board 格式的模块（防腐层）。第二个组织运行时接入 = 增一个 adapter，核心不动。

### 3.2 双模运行矩阵（反事实检验）

| 场景 | 结果 |
|---|---|
| OuSheng 单独运行 | 不变。MODOU 不在任何写路径/同步路径上 |
| MODOU 单独运行 | `asr assess`（项目级评估）、`asr run`（收敛闭环）完整可用，adapter 不加载 |
| 合并运行 | MODOU 自调度（cron/手动）：fetch 只读克隆 → 快照固定 → 分级 → 分析 → 静默报告 /（P3）写回。无常驻、无锁竞争 |
| board schema 漂移 | MODOU FAIL-LOUD 出 BLOCKED，不波及 OuSheng |
| MODOU 宕机/误报 | OuSheng 零影响；最坏产物一条多余 backlog item，被 PM 分诊消化 |

### 3.3 交互总图

```text
┌──────────────────────────── OuSheng 场内（治理边界）────────────────────────────┐
│                                                                                 │
│   board 远端 git（gitee + aliyun）◀═════ 协作总线（pull/push）═════▶ 各席位克隆     │
│         ▲   pull（只读镜像）                    pull / push                      │
│         │ push（P3，稀少）                            │                           │
│         │                                    ┌────────┴─────────────────────┐   │
│   ┌─────┴────────┐    分诊/授权/裁决           │ 执行席位（224/225/226/aliyun）  │   │
│   │ PM 席位      │◄─── backlog→ready ──────────│ pm-dev / data / mlake / ui /  │   │
│   │ (human)     │    指派                      │ test / agentos / lhcp …      │   │
│   └─────▲───────┘                             │ opencode+插件，cruise 巡航     │   │
│         │ 抽检标记（真假反馈）                   │ = 场内执行者（executor 图内）   │   │
│         │                                     └──────────────┬───────────────┘   │
│         │                                        执行/提交证据/推送                 │
└─────────┼──────────────────────────────────────────────────┼─────────────────────┘
          │ ① 纠偏 item 进 backlog（P3 起）                      │
          ▼                                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ OuSheng 场外：MODOU Assurance（独立机 / 独立调度；非席位、不入 cruise、不在 executor 图）│
│                                                                                 │
│   board 克隆 ──git fetch（只拉）──► 远端        code repos 只读克隆（repos.yaml 映射）│
│   wth 规则文档快照（铁律/协议，锁 git 版本）                                        │
│                                                                                 │
│   案卷装配 → LLM 评佃员（opencode 通道）→ 四道门 → assessment 存档（默认静默）        │
│        │                                                                        │
│        └─P3→ ousheng-mcp（path=本地克隆）→ 经全部硬门 commit → git push（第 N+1 个   │
│             协作克隆；fleet 本来就这样同步，只是 MODOU 跑的是评估不是开发）             │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 3.4 身份挂载（设计定案）

按"做什么 → 要什么身份"推演的结论：

1. **只读期（P1/P2）：零身份**。git 只读克隆即纯镜像，不注册、不落任何配置——观察者零足迹，独立性最大。
2. **写回期（P3）：仅一个注册 agent actor** `modou`（`type=agent`，`responsible_human=pm`，F4 反孤儿规则），由 PM 一次性添加 `actors/modou.yaml`。这是 MODOU 在 OuSheng 里的全部身份。
3. **不是席位**。席位 = 场内执行者座位（接任务/报进度/入 cruise/进 executor 图/fleet 部署）。MODOU 全都不做。反事实：挂成席位 → converge 把它当运动员审计（语义错位）、cruise 可能派活（职责污染）、fleet 运维面扩大。**注册身份 ≠ 占席位。**
4. **不挂 test 角色**。test 的 `test_result` 证据正是 MODOU 的核验对象，挂成 test 席位 = 被评估方家族自证，Assurance 独立性结构上瓦解。

| 维度 | test 席位 | MODOU |
|---|---|---|
| 场位 | 场内执行者 | 场外保证者 |
| 接受任务 | 是 | 否（只产纠偏建议，永不被指派） |
| 报告 progress | 是 | 否 |
| 证据方向 | 为他人任务提交 test_result | 只在自己创建的纠偏 item 上挂 assessment 指针 |
| 评估对象 | 任务交付物 | 任务 + 证据 + 组织过程（含 test 产出本身） |
| cruise | 循环内 | 循环外，非阻塞观察 |

5. **不建 assignment**（判据驱动：无 executor 责任就不占 executor 图）。P3 前核验 `create_work_item` 是否强制 assignment 行。
6. **身份解析**：调 MCP/CLI 显式传 `actor=modou`（解析链第一优先级）；MODOU 机不落 `.opencode/ousheng.json`、不落 workspace `me`——杜绝身份兜底。
7. **push 凭据**：board 协作凭据（与 fleet 席位同级）。冲突处理 `pull --rebase` 后重试，重试前先查 fingerprint 去重。"PM 机代写"方案否决：身份混用、MODOU 不自持。

### 3.5 一次完整闭环时序（P3 后形态）

```text
MODOU机            board远端           PM席位              执行席位
   │ git fetch        │                  │                    │
   ├─────────────────►│                  │                    │
   │ 快照固定 → 分级 → 案卷 → LLM 两级评估 → 四道门                │
   │ assessment 存档（静默影子，默认到此为止）                       │
   │── P3 且校准达标后 ──────────────────────────────────────────  │
   │ MCP 写本地克隆（硬门全过）→ push  │                       │    │
   ├─────────────────►│ 纠偏 item 入 backlog │                 │    │
   │                  ├──────────────────►│ PM 分诊（=授权）      │    │
   │                  │                  ├─── 指派 ───────────►│    │
   │                  │                  │                    ├──► │ 执行/新证据/push
   │ git fetch        │◄─────────────────┴────────────────────┤    │
   ├─────────────────►│                  │                    │    │
   │ reassess（只认新证据，reassessment_of 关联）                  │    │
   │ → RESOLVED / STILL_DEVIATED / INCONCLUSIVE                 │    │
   │ PM 抽检标记 ────────────────────►│ 质量账（precision 维持写回资格）│    │
```

P1/P2 全程只有前三行——没有任何东西流向 board。PM 出现两处不重不漏：backlog 分诊（= Authorize）+ 抽检标记（= 质量账数据源）。

---

## 4. 任务分级路由

**分级原则**：分析期望价值 ∝ 偏差概率 × 后果量级；成本 = token + PM 注意力 + 误报信任损耗。分级 = 价值驱动的采样，不是均匀覆盖；报告必须声明未覆盖范围（诚实优于假完整）。

| 级 | 判据（字段键为主，歧义交 LLM） | 处理 | 深度 |
|---|---|---|---|
| **T0 必进** | ① 属于 decided design 的 `related_items` 闭包（feature/requirement 型）；② `contract.breaking=true`；③ 带 `target_version` 的 release/deployment 相关；④ 跨系统 seam 两端（contract 涉及双方 system） | 无条件进，两级完整分析 | 深：逐项证据核验 + 覆盖映射 + 缝隙检查 |
| **T1 采样** | 其余 `done`/`testing` 态 task/bug | 采样率配置（初始 10–20%）；LLM 廉价风险分，高风险升级逐项 | 中：单 item 案卷 |
| **T2 不进** | notice、cancelled、backlog/ready 未完成项、LLM 判定"纯文本小修且无系统面影响"者 | 不分析；报告声明计数与理由 | — |

规则：

- 分级键 = 字段读取（type / priority / contract.breaking / target_version / design 关联存在性）——便宜、客观、可审计；归属歧义交 LLM（item→design 是自由文本弱链，属语义判断）。
- T0 闭包以 design 侧结构化 `related_items[]` 为准，LLM 从 item 文本捞 "design:xxx" 弱链补充。
- 小任务高风险漏检的残余防线：T1 采样池含全部 done 项；design 级分析覆盖设计锚定一切；converge 结构检查仍在。残余漏检风险显式接受并在报告声明。
- 分级结果全落档（每 item 一行：级 / 判据 / 是否 LLM 判定），分级错误率进质量指标。
- design 级分析只对 T0 闭包做——聚合必须有基准，无 design 锚的 item 无聚合语义。

---

## 5. 两级偏差分析

### 5.1 分析对象模型

```text
design (decided, systems[], related_items[], decided_by=pm)      ← 基准载体
  ├── item A (assignee=data-agent,   evidence[...], contract X)  ← agent 任务级
  ├── item B (assignee=mlake-agent,  evidence[...], contract X)  ← 跨系统缝隙两端
  ├── item C (assignee=ui-agent,     evidence[...])
  └── 集成/联合测试 item（多系统端到端）                            ← design 级结果证据
```

OuSheng 的 **Contract 对象（kind/status/breaking，跨系统接口契约）= 多 agent 拆分的"接缝" formal 化**——拆分类偏差最可能藏身处：A 侧已 live、B 侧仍按旧约调、contract 停在 proposed、breaking 未经 C2。

### 5.2 案卷（Dossier）——评佃员的全部输入

MODOU 装配（确定性工作）：

| 案卷页 | 内容 | 来源 |
|---|---|---|
| 基准页 | design.md 全文 + frontmatter（decided_by/at） | `git show` 快照 |
| 任务页 ×N | related item 的 WorkItem YAML + evidence 清单 | 同上 |
| 缝隙页 | 涉及的 Contract 状态 + 双方 item/系统 | 同上 |
| 证据页 | 各 evidence locator 的解析结果（文件内容 / commit subject / rev-parse 结果） | board + code repos 只读克隆 |
| 规则页 | OuSheng 已发布规则文档（铁律清单、board-protocol、版本轴规则等）+ 其 git 版本 | wth repo 快照 |
| 上下文页 | as_of_commit、装配时间、工具清单、协议版本 | MODOU |

评佃员（opencode LLM agent，复用现有 Analyzer 结构化输出与降级解析——Implemented）在案卷上工作。board 内容以 fenced block 呈现（数据非指令，防注入）。**规则页模式 = "规则书阅读者"而非"规则复刻者"**：治理知识来自 OuSheng 已发布文档并由独立判断者应用，MODOU 不把治理规则重写成代码——独立性的来源是独立阅读 + 独立事实工具 + 独立判断通道。

### 5.3 item 级协议（每个 agent 任务的完成）

1. 读任务页：title/description 声明了什么交付（含文本里的量级/依赖声明）。
2. 读证据页：每条证据**说了什么** vs **能证明什么**（存在 ≠ 真实、commit 存在 ≠ 任务完成）。
3. 事实核实（按需调工具）：引用的 commit / 文件是否如声称。
4. 产出：结构化 findings（code / severity / confidence / expected / actual / artifact 引用）或 ALL-CLEAR；证据不足 → INCONCLUSIVE，禁止猜。

### 5.4 design 级协议（独立判据，不受 item 级 PASS 约束）

1. **义务抽取**：从 design.md 抽出可验收义务清单（编号）。design 义务含混 → `BASELINE_DEFICIENT` finding 指向 design 文档（高价值发现，不是分析失败）。
2. **覆盖映射**：义务 → items 矩阵；无承接义务 = `TASK_COVERAGE_GAP` 候选（须排除"无需任务化"情形，语义判断归 LLM）。
3. **缝隙检查**：跨系统 contract 状态轴 vs 双方证据；breaking 是否经 C2；`DEPENDENCY_VIOLATION` 候选。
4. **结果检查**：design 目标 vs 集成/联合测试证据；声明的 target_version 是否有 deployment/release 证据覆盖（版本轴规则在规则页，由评佃员执行）。
5. **聚合**：四态 + assurance 维度（governance / execution / deliverable / outcome，未评估显式 `UNASSESSED`）。

**聚合语义（必须代码化并有测试）**：

| item 级 | 覆盖 | 缝隙/结果 | design 级 |
|---|---|---|---|
| 全 PASS | 缺口 | — | DEVIATION（TASK_COVERAGE_GAP） |
| 全 PASS | 完整 | 缝隙未证 | INCONCLUSIVE（不是 PASS） |
| 个别证据缺口 | 完整 | 目标证据充分 | DEVIATION（EVIDENCE_GAP，severity 按 item 定） |
| 全 PASS | 完整 | 证据充分 | PASS |

### 5.5 反事实检验表（设计自检，实现后逐条配测试）

| 反事实 | 系统行为 |
|---|---|
| 所有 agent 如实完成，design 达成 | 两级 PASS——不制造差异证明价值；与 converge 的重叠标 CONFIRMED_OVERLAP |
| 每 item 真完成，拆分漏一块 | item 级全 PASS；design 级覆盖映射捕获 |
| A/B 系统各自按各自理解完成 | item 级各自 PASS；缝隙检查捕获 |
| design.md 含混 | BASELINE_DEFICIENT，打回基准层，不硬分析 |
| finding 引用的 artifact 不存在 | 引用抽查门丢弃该 finding + 计入质量分 |

---

## 6. 质量体系

### 6.1 绳牛切分（MODOU 与 LLM 的职责表）

| MODOU（绳：方向 + 事实 + 门） | LLM 评佃员（牛：全部语义工作） |
|---|---|
| 快照固定与案卷装配 | item↔design 弱链解析、任务归属判定 |
| 分级策略与字段键 | 歧义项分级判定、T1 风险分 |
| 两级分析协议 prompt | 逐项偏差判断、覆盖映射、缝隙判断 |
| 事实工具供给（读文件 / git show / rev-parse） | severity 与 confidence 初判、根因归纳 |
| 四道门 + 四态合成 + 存档因果链 | design 级聚合判断、纠偏建议生成 |
| 写回策略执行 | — |

### 6.2 四道门（全部确定性）

1. **引用抽查门**：每条 finding 的 artifact 引用存在性机械核验；不实 → 丢弃 + 记质量账。幻觉第一道闸。
2. **置信度下限**：confidence < 阈值不进报告（进档案供校准统计）。
3. **报告预算**：每份报告 top-N（severity × confidence），超出进附录。防噪音压垮 PM。
4. **幂等门**（P3）：fingerprint = hash(subject_id + deviation_code + baseline 版本 + evidence 版本)；同指纹开放纠偏项存在则不建；写回超时先查询后重试（at-most-once）。

### 6.3 校准闭环（写回的唯一前置）

- **注入集**：board 只读克隆上构造可控偏差（EVIDENCE_GAP / FALSE_COMPLETION / 覆盖缺口 / 缝隙违规 / 版本轴不匹配 / 正常样本 / 证据不足样本）。board 即 git，fixture 成本极低。
- **PM 抽检**：静默期报告按比例送 PM 标记真假，反馈进质量账。
- **门槛（P2 冻结数值）**：确定性注入偏差检出率 ≥ 100%；正常样本误报 = 0；写回候选 finding 实测 precision ≥ 门槛（如 0.8）且样本量 ≥ 下限。**指标不可测 → 不写回，无例外。停在影子模式是合法终态。**
- 指标全带分母声明。四分类分布（CONFIRMED_OVERLAP / MODOU_ONLY / CONVERGE_ONLY / DISAGREEMENT）作为报告指标输出。

### 6.4 运行姿态与诚实边界

- 默认静默影子：assessment 只落 MODOU 自己的存储；写回默认关闭。
- 每份报告的 scope 声明：未覆盖分级区间（T2 计数与理由）、T1 采样率、digest 自首见锚定局限、弱链解析不确定项、LLM 模式与模型/协议版本。
- 可重放性承诺限于案卷与协议（可审计、可归因），不承诺逐位复现。
- Verifier 超时 / 外部依赖失败 → INCONCLUSIVE，不得默认 PASS，不得转为事实失败。

---

## 7. 系统架构

### 7.1 模块树（核心 6 文件 + 适配 2 文件）

```text
asr/
├── assurance/                  # 核心（内聚；零 OuSheng 词汇）
│   ├── dossier.py              # 案卷装配：快照/证据预取/规则页/上下文页
│   ├── grading.py              # 分级路由：字段键 + 策略 + LLM 歧义判定入口
│   ├── assessor.py             # 评佃员 harness：两级协议 prompt、结构化解析（复用 Analyzer）、工具供给
│   ├── compose.py              # 聚合语义 + 四态合成（BASELINE_DEFICIENT / UNASSESSED）
│   ├── gates.py                # 引用抽查 / 置信度 / 预算 /（P3）fingerprint 幂等
│   └── store.py                # assessment 存档（复用 EventStore 因果链：reassessment_of）
├── adapters/ousheng/           # 唯一耦合点（防腐层）
│   ├── snapshot.py             # git show 快照读 + schema_version fail-loud
│   └── mcp_client.py           # P3 写回：create_work_item / add_evidence（stdio JSON-RPC）
└── cli/main.py                 # + board-assess / board-writeback（默认 dry-run）
```

`asr assess`（模式 A：项目级评估）、`asr run`（模式 B：收敛闭环）原样保留；本方案为**模式 C：绳上保证**。三者共享 Analyzer 解析、EventStore、opencode 通道。

### 7.2 Assessment 数据模型（`modou/assessment@1.0`）

```json
{
  "schema_version": "modou/assessment@1.0",
  "assessment_id": "ASM-2026-11-19-001",
  "producer": { "runtime": "modou", "version": "0.x", "protocol": "item-v1+design-v1" },
  "scope": {
    "board": "tide-datax", "systems": ["mlake"],
    "as_of_commit": "<sha>", "items_scanned": 201,
    "tier_coverage": { "T0": 48, "T1_sampled": 22, "T2_skipped": 131 }
  },
  "baseline_refs": [
    { "baseline_id": "BL-...", "type": "design", "source": "designs/layered-data-dev/design.md", "git_rev": "<sha>" },
    { "baseline_id": "BL-...", "type": "governance", "source": "wth@<sha>:docs/铁律清单.md" }
  ],
  "assurance": { "governance": "DEVIATION", "execution": "INCONCLUSIVE", "deliverable": "DEVIATION", "outcome": "UNASSESSED" },
  "status": "DEVIATION",
  "findings": [{
    "finding_id": "F-...",
    "code": "TASK_COVERAGE_GAP",
    "severity": "high",
    "confidence": 0.9,
    "subject": { "work_item": "T-153", "revision": 7, "design": "layered-data-dev" },
    "baseline_ref": "BL-...",
    "expected": "design 义务 #3（资源预算准入）应有承接 item",
    "actual": "related_items 闭包内无承接项，亦无文本弱链",
    "evidence_refs": ["dossier:tasks", "design.md§2.1"],
    "verifier": "assessor-llm/model@ver",
    "rationale": "...",
    "recommendation": "创建资源预算准入实现任务或记录豁免",
    "fingerprint": "sha256:...",
    "created_at": "..."
  }],
  "reassessment_of": null,
  "created_at": "..."
}
```

状态机：`PASS / DEVIATION / BLOCKED / INCONCLUSIVE`（复验结论另用 `RESOLVED / STILL_DEVIATED`）。`WAIVED` 不由 MODOU 授予——豁免由有权人在权威系统授权，MODOU 只记录来源与范围。

偏差代码全集：`OMISSION / SEMANTIC_DRIFT / CONSTRAINT_VIOLATION / TASK_COVERAGE_GAP / DEPENDENCY_VIOLATION / EXECUTION_DEVIATION / DEADLINE_BREACH(observed) / RESOURCE_OVERRUN / QUALITY_FAILURE / EVIDENCE_GAP / TARGET_MISS / FALSE_COMPLETION / AUTHORIZATION_GAP / BASELINE_DEFICIENT / INCONCLUSIVE`。每个代码必须带定义、适用基准、证据要求、默认严重度、是否允许自动建纠偏项、复验条件。

### 7.3 CLI 契约

```bash
asr board-assess --board <克隆路径> [--design <topic>] [--system S] [--items ID…]
                 [--tier T0|T1|T2 --sample-rate P] [--reassess-of ASM-ID]
                 [--as-of <rev>] [--no-llm] --json-output <file>
asr board-writeback --assessment <file> --dry-run        # P3；默认 dry-run
```

退出码（评估结论与运行错误分离）：0 = PASS；1 = DEVIATION / INCONCLUSIVE；2 = BLOCKED / 环境错误。cruise 只作非阻塞观察信号。

---

## 8. 实施路线（每阶段验收不达标即停；停在影子模式是合法终态）

| 阶段 | 内容 | 时间盒 | 关键验收 |
|---|---|---|---|
| P0 卫生 | 修 run-dag TypeError / compare stub / 僵尸测试文件；README 能力表对齐实测 | 1 天 | pytest 全绿；无 Planned 标成 Implemented |
| P1 影子两级分析 | snapshot.py + dossier + grading + assessor（两级协议）+ compose + store；真实 board 只读克隆全量跑 | 2–3 周 | ① 425 items 全量解析、零不明 BLOCKED；② ≥3 个真实 design 出两级 assessment（含 layered-data-dev）；③ 每条 finding 可溯至案卷页；④ INCONCLUSIVE 路径有实例；⑤ 四分类分布作为指标输出 |
| P2 校准与门 | 注入集 + 引用抽查门 + 置信度/预算门 + PM 抽检协议 + 指标测量；冻结门槛值 | 1–2 周 | 注入检出率 / 正常样本误报 / precision 达冻结门槛；达不到 → 停在影子模式 |
| P3 写回（条件触发） | mcp_client + fingerprint 幂等 + 三态（OPEN/SUPERSEDED/CLOSED）+ PM 注册 modou actor（不建席位/不进 cruise/不建 assignment/显式 actor 传参） | 1 周 | 完整 Detect→Recommend→(backlog 分诊=Authorize)→Execute→reassessment 链路；重复巡检零重复纠偏项；CAS 冲突安全恢复有测试；核验 create_work_item 是否强制 assignment |
| P4 常态化 | 定时巡检、趋势、cruise 非阻塞观察 | 持续 | 评估不阻塞生产；指标口径稳定 |

**纠偏生命周期**（MODOU 侧，三态）：`OPEN → SUPERSEDED / CLOSED`。board 状态映射（item done/cancelled → CLOSED 等）显式声明为启发式。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| board schema 漂移 | snapshot.py 锁 schema_version，未知版本 FAIL-LOUD → BLOCKED |
| 读到写者中途状态 | 一律 `git show <commit>:<path>` 快照读；as_of_commit 固定基准 |
| LLM 幻觉 finding | 引用抽查门 + 置信度下限 + structured 解析失败降级 INCONCLUSIVE |
| prompt 注入（board 内容是数据不是指令） | fenced block 呈现、协议固化输出结构、注入样本进测试集 |
| 规则漂移（wth 文档变更） | 规则页锁 git 版本；案卷记录来源 rev；变更触发重校准 |
| 重复纠偏项 | fingerprint 幂等门 + 写回前查询 + 超时先查后重试 |
| MCP 写回 CAS 冲突 | 冲突即弃即重读；pull --rebase 后重试，不覆盖 |
| token 成本失控 | 分级即成本闸；T1 采样率可调；报告预算 top-N |
| MODOU 误报损公信力 | 静默观察期 + PM 抽检 + precision 门槛 + 可审计可归因 |
| 双检噪音（与 converge 重叠） | 四分类标注，报告分层"独立新发现 / 双记账确认" |

---

## 10. 不做清单

改 OuSheng 任何代码；治理规则代码复刻；六对象 DEP 与事件机制；8 态纠偏生命周期；注册席位 / 进入 cruise / 进入 executor 图 / 挂 test 角色；均匀覆盖所有任务（T2 永不进）；在 precision 不可测时写回；合并三种完成状态；把 LLM 设为最终事实裁判；为集成删除 `asr run` 收敛能力。

---

## 11. 开发前核验清单（P0/P1 动手前逐项确认）

- [ ] 核验 board 当前 schema_version 与目录结构（`.ousheng/work|actors|systems|roles|designs`）
- [ ] 核验 `git show <rev>:.ousheng/...` 快照读在 kanban 克隆上可行；确认克隆只读姿势
- [ ] 核验 MCP `create_work_item` / `add_evidence` 参数、是否强制 assignment、CAS 行为
- [ ] 核验 WorkItem / Evidence / Actor / Contract 的实际 YAML 字段与严格 decode 规则
- [ ] 核验 MODOU `assess` / `run` / EventStore / Analyzer 现状与死代码清单
- [ ] 核验 repos.yaml 系统映射与 code repos 只读克隆的可得性
- [ ] 冻结：Assessment schema 字段、偏差代码语义表、两级协议 prompt v1、四道门参数
- [ ] 冻结：分级判据键、T1 初始采样率、注入集清单与门槛数值（P2 冻结后两项）

---

## 12. 一句话总结

**MODOU 对 LLM 评佃员是绳：装配案卷（事实）、固化两级协议（方向）、把住四道门（质量）；对 OuSheng 是只读的影子与守规矩的建议者：快照读、官方写、backlog 等授权。分级决定不分析什么，校准决定何时才配开口——分析不好，就不分析。**
