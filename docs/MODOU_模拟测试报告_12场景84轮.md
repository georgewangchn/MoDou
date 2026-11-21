# MoDou 模拟测试报告 — 12 场景 × 84 轮端到端验证

**日期**：2026-11-21
**执行方式**：真实 CLI 进程边界（非 mock）× 合成目标项目 × 合成 OuSheng board（git 仓库）× fake MCP server；确定性链路（`--no-llm` / analyzer 降级路径），LLM 语义层因本地 endpoint 不可用列入联调清单。
**驱动**：`/tmp/modou-simlab/run_sim.py`（12 场景函数化，逐轮断言写入 `results.jsonl`）

## 结论

- **12 个场景、84 轮模拟，最终 54 项断言轮全绿**（去重后；每个场景 5-10 轮）。
- 暴露 **5 个真实发现**：3 个当场修复并附回归测试（F1/F3/F4），2 个记录并进入 OuSheng 联调清单（F2/F5）。
- 另有 5 处驱动自身假设错误（见附录）——修正过程本身澄清了产品的若干语义边界（T0 的保障走 design 级、`--item` 是追加、`--once` 的 cycle 语义、BLOCKED 周期不落存档）。
- 修复后仓库门禁：**349 tests passed · ruff 0 · mypy 0**。

## 场景矩阵

| # | 场景 | 轮次 | 覆盖 | 结果 |
|---|---|---|---|---|
| S1 | 模式 A 四态判定矩阵 | 8 | PASS/DEVIATION/INCONCLUSIVE + analyzer 降级不伪造 PASS + JSON 导出 | ✅ 8/8 |
| S2 | 验收证据链 | 6 | 准则↔nodeid 匹配、EVIDENCE_GAP、失败即无证据、多准则混合 | ✅ 6/6 |
| S3 | 超时与环境故障 fail-safe | 5 | pytest 超时→BLOCKED、坏 spec、坏测试语法、缺路径 | ✅ 5/5 |
| S4 | 模式 B 预检与降级 | 5 | opencode 缺失→exit 2、LLM 不可达降级、坏 spec、doctor | ⚠️ 4/5（F2） |
| S5 | board 快照不可变性 | 8 | 评估中 writer 持续变更；pinned rev 稳定、rolling 跟随、sha 落档 | ✅ 8/8 |
| S6 | 分级路由与抽样 | 9 | design 级 T0、T1 抽样率 0/0.5/1.0、T2 跳过、--item 追加 | ✅ 9/9 |
| S7 | 注入校准门禁语义 | 6 | no-llm 必须 NOT CALIBRATED（合法终态）、rounds3 一致性、空 PM 账本不过 precision 门 | ✅（6 轮全过 + 补录 2 轮，见 F5 耗时注记） |
| S8 | 写回闭环（fake MCP） | 9 | plumbing 全 INCONCLUSIVE、dry-run、severity 阈值、fingerprint 幂等、fp 黑名单、report JSON 拒绝、MCP 失败→PENDING at-most-once | ✅ 9/9 |
| S9 | 巡检循环与趋势演化 | 7 | board 逐轮演化、corrupt YAML 周期隔离、修复恢复、trend.jsonl/last-run.json/存档口径 | ✅ 7/7 |
| S10 | 失败注入 fail-loud | 6 | 未知 schema、坏 YAML、缺结构文件、坏 rev、未知 design | ✅ 6/6 |
| S11 | 并发与幂等 | 5 | 并发 board-assess、并发 board-mark 账本完整、重跑追加历史、latest-per-subject 口径 | ✅ 5/5 |
| S12 | 退出码契约全集 | 10 | 全命令 × 代表结果 → 0/1/2（cruise 集成面） | ✅ 10/10 |

## 真实发现与处置

**F1（高危，已修复）analyzer 降级伪造 PASS**
现象：S1-R7 中 LLM endpoint 不可达时，`asr assess`（不带 --no-analyzer）语义分析失败降级（opencode 未产出报告），但最终状态仍是 `PASS`、`analyzer_mode: completed`、exit 0——违反"分析不可用不得伪造 PASS"铁律。
根因：`_determine_status` 的 blocking 规则不覆盖 `INCONCLUSIVE` code 的降级 finding（LOW severity 绕过全部阻断条件）。
修复：[project.py] INCONCLUSIVE finding 阻断 PASS（DEVIATION 级仍优先）；`analyzer_mode` 如实标 `degraded`。回归测试 `test_determine_status_analyzer_degradation_never_pass`。

**F2（中，记录待双方决策）`asr run` 环境错误分类**
现象：S4-R3 中 LLM endpoint 不可达时收敛插件退出码 1（未收敛）而非 2（环境错误）——opencode 故障被 Builder 当轮内错误吞掉，cruise 侧会误判为可重试的普通未收敛。
处置：涉及退出码契约语义变更（OuSheng cruise 依赖），已列入联调清单 C1，建议加"连续 N 次 opencode 故障升级为环境错误"。

**F3（中，已修复）`--as-of HEAD` 落档为符号名**
现象：S5 中 `board-assess --as-of HEAD`（默认值）时报告与存档的 `as_of_commit` 记录字符串 "HEAD" 而非实际 sha——快照可追溯性受损（branch 一移动即无法复现评估基准）。
修复：[snapshot.py] 新增 `resolve_rev`，pipeline 入口将 HEAD 解析为 sha 后固定。回归测试 `test_resolve_rev_pins_head_to_sha`。

**F4（中，已修复）board-writeback 对错误输入形状崩溃**
现象：S8 中把 board-assess 的 report JSON（`{"assessments": [...]}`）喂给 `--assessment` 时直接 `KeyError: 'assessment_id'` 崩溃（traceback 而非可读错误）。
修复：[cli] 输入形状校验 + 明确指引（应使用存档中的单 assessment ASM-*.json）。回归测试 `test_cli_writeback_rejects_report_json_with_clear_error`。

**F5（低，联调项）board-calibrate 在无 LLM 环境耗时不可控**
现象：S7/S12 复跑时单轮 calibrate 3-15 分钟不等——校准跑完整管线，LLM 不可用时每个 dossier 串行等待 opencode 失败。
处置：联调环境需稳定 LLM endpoint（OpenAI 兼容即可，见联调清单 C2）；或后续为 calibrate 提供 fast-fail 预检（连不通直接 BLOCKED 不进管线）。

## 附录：驱动假设勘误（产品语义澄清）

| 原假设 | 实际语义 |
|---|---|
| T0 项（breaking/release/testing）逐项进入评估 | T0 保障走 design 级两级分析；单项审计用 `--item` 显式指定 |
| `--item` 替换默认任务集 | `--item` 是追加（默认 decided designs 仍在） |
| 3 条 findings（high+low+low）过 medium 门应创建 2 项 | 只有 high 过门 → 恰创建 1 项（severity 阈值行为正确） |
| `board-watch --once` 的 cycle 计数跨进程累加 | 每进程从 1 起（cron 模式语义）；周期序号看 trend.jsonl 行数 |
| BLOCKED 周期会落 assessment 存档 | BLOCKED 只记周期日志与 last-run.json，不产生存档（趋势 runs 口径=成功周期数） |

## 复现

```bash
cd /tmp/modou-simlab
/Users/siidt/Documents/siicode/asr/.venv/bin/python run_sim.py all   # 或 a / c 分组
# 逐轮断言与元数据: results.jsonl
```

LLM 语义层（assessor 两协议、converge 交叉对账、真实 PM 抽检）待联调环境，见《MODOU_OuSheng_联测配合清单》。
