# MODOU × OuSheng 联测配合清单

**版本**：Draft 1（2026-11-21）
**背景**：MoDou 侧已完成 12 场景 × 5-10 轮端到端模拟测试（确定性链路，见《模拟测试报告》），四条硬约束与退出码契约在合成 board + fake MCP 上全部验证通过。以下事项需要 OuSheng 侧配合开发/提供，才能完成真实环境的联合测试。

---

## A. 立即需要（联测阻塞项）

| # | 事项 | 说明 | 验证目标 |
|---|---|---|---|
| A1 | **只读 board 克隆访问** | 提供生产 board 仓库的只读克隆方式（git remote + 只读凭据或匿名 http）。MoDou 巡检（board-watch）需要 `git fetch` 权限，永不 push | S5/S9 快照锚定与巡检在真实 board 上复验 |
| A2 | **`ousheng-mcp` 真实服务端** | MoDou 写回契约：newline JSON-RPC over stdio（MCP stdio），方法 `initialize` / `tools/list` / `tools/call`，工具 `create_work_item` + `add_evidence`（参数见 `asr/assurance/feedback.py::execute_writeback`）。需要 OuSheng 确认工具 schema 与我们发出的参数一致（`id/type/title/system/detected_by/actor` + T10 指针证据 `modou-assessment://<id>#<n>`） | S8 写回闭环在真实硬门（identity/CAS/状态机）下复验 |
| A3 | **注册 `modou` actor 并授权（P3 生产前置）** | writeback 的 create 调用以 `actor=modou` 落账，需 OuSheng 侧注册该 agent actor 并授予 backlog 创建权限（分诊=PM 授权的语义不变） | 写回闭环生产启用判定 |
| A4 | **system → git repo 映射** | 当前 git_commit 类型 locator 的存在性检查全部 UNRESOLVED（缺 system→repo 映射，真实评估 PMMARK 发现 [10]）。需要 OuSheng 提供 board `system` 字段到实际仓库 clone 路径/remote 的映射表（配置文件即可） | 证据 locator 解析率、EVIDENCE_GAP 误报率下降 |

## B. 联测场景约定（建议排期）

| # | 场景 | OuSheng 侧配合 | MoDou 侧就绪 |
|---|---|---|---|
| B1 | 影子评估全量跑（1 个真实 design + 其全部 items） | 指定一个已 decided 的 design 与目标 rev；提供该 design 的判定基准背景 | ✅ board-assess 就绪 |
| B2 | T1 抽样率联调（0.1/0.3/0.5 对照） | 无需配合（读 board） | ✅ 就绪；注意抽样当前为 `random.sample` 非确定，是否需要按 id 排序的确定性抽样请反馈 |
| B3 | 与 `converge` 输出的交叉对账 | `--converge-report` 需要 OuSheng `converge` 命令的文本输出格式样例（CONFIRMED_OVERLAP/MODOU_ONLY/CONVERGE_ONLY 的解析契约在 `asr/assurance/xcheck.py`，请确认格式一致或提供样例文件） | ✅ 代码就绪，待真实样例校准 |
| B4 | cruise 模式驱动 MoDou（模式 A/B 子进程化） | cruise 以退出码分支：0=PASS/收敛，1=DEVIATION/INCONCLUSIVE/未收敛，2=BLOCKED/环境错误。请确认 cruise 侧重试策略不要把 exit 2 当可重试瞬态（环境错误应告警人工介入） | ✅ 契约已全量验证（S12） |
| B5 | work-item 描述 → spec YAML 投影 | 最小投影格式：`goal` + `acceptance_criteria[]`（字符串列表）。若 OuSheng 任务描述有结构化验收字段，请提供样例，我们适配投影器 | ✅ 投影器就绪 |
| B6 | PM 抽检节奏（precision 账本） | 校准门槛要求 PM 标注 n≥20 才能过 precision 门（≥0.8）。需要 PM 对真实 findings 做一轮抽检（命令模板见 `.modou/PMMARK.md`） | ✅ board-mark 就绪 |
| B7 | 纠偏项生命周期对账 | MoDou 创建的纠偏项一律进 backlog（MD-9xx 编号）。联测时请 OuSheng/PM 确认：分诊、转正式任务、关闭后 `modou-assessment://` 指针证据的可见性 | ✅ 写回幂等/at-most-once 已验证（S8） |
| B8 | board-watch 常驻部署 | 需要：可常驻的主机/容器、fetch 凭据、告警接点（last-run.json 作为非阻塞信号由 cruise/PM 拉取）。建议先 `--once` cron 模式联调 | ✅ cycle 隔离/趋势就绪（S9） |

## C. 已发现的契约层差异（需双方确认）

1. **`asr run` 环境错误分类**（模拟发现 #2）：LLM endpoint 不可达时收敛插件退出码为 1（未收敛）而非 2（环境错误）——opencode 故障被当作轮内错误。若 cruise 对 1/2 的处理不同，请反馈期望语义，MoDou 可加"连续 N 次 opencode 故障升级为环境错误"。
2. **board-calibrate 无 LLM 环境的耗时**（模拟发现 #5）：校准跑完整管线，LLM 不可用时每个 dossier 串行等待 opencode 失败（每轮数十秒到分钟级）。建议 OuSheng 侧为联测环境提供稳定 LLM endpoint（OpenAI 兼容，`opencode.json` 配置即可），或接受联测先跳过 LLM 语义层。
3. **schema_version 兼容**：当前支持 work schema_version=2。OuSheng 若有 v3 计划，请提前同步 schema 变更（MoDou 侧 fail-loud：未知版本直接 BLOCKED，已验证 S10）。

## D. 联测环境基线（MoDou 侧已固定）

- 退出码契约：`assess`/`run`/`board-*` 全部 0/1/2（S12 十项全验）
- 快照锚定：`--as-of HEAD` 自动解析为 sha 落档（S5 发现 #3 已修复）
- 评估只读纪律：全程不写 board；写回仅经 MCP + 校准达标 + PM 授权三前置
- 模拟测试报告与逐轮证据：`/tmp/modou-simlab/results.jsonl`（或随附归档）

---

**联系**：将本清单转给 OuSheng 侧后，联测排期建议按 A（阻塞项）→ B1/B3/B4（核心链路）→ B6-B8（生产启用）顺序推进。
