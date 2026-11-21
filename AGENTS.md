# AGENTS.md — MoDou（墨斗）工作区说明

## 项目定位

MoDou（墨斗）· Assurance Runtime：对既有交付物做独立验证（Tester 确定性 pytest）与偏差分析（Analyzer），输出 `PASS` / `DEVIATION` / `INCONCLUSIVE` / `BLOCKED` 四态验收结论。Builder → Tester → Analyzer 收敛链路保留为软件工程插件，**不参与评估判定**。模式 C（`board-assess` 系列）对 OuSheng board 做两级影子偏差分析，为 Experimental。

## 命名铁律（2026-11-15 品牌改名）

品牌层已改名 MoDou，但**技术标识一律保留、禁止重命名**：包目录 `asr/`、`from asr ...` import、类名 `ASRConfig` / `ASRController` / `ASRRuntime` / `ASRLogger`、CLI 命令 `asr`、环境变量 `ASR_*`、配置 `asr_config.yaml`、`.asr_sandbox/`、包名 `asr-runtime`。用户可见输出字符串用 "MoDou" 品牌。详见 README「改名范围」一节。

## 常用命令

```bash
source .venv/bin/activate          # 主虚拟环境，Python 3.12.11（uv 管理，无内嵌 pip，装包用 uv）
pip install -e ".[test]"           # 安装（含 pytest）；开发装 ".[dev]"（含 ruff/mypy/build）

pytest                             # 全量测试（asyncio_mode=auto，<15s；数量以 CI 为准）
pytest tests/test_board_watch.py -k "cycle"   # 聚焦单测
ruff check .                       # lint 门禁（必须零违规；规则见 pyproject）
mypy asr                           # 类型门禁（宽松基线：ignore_missing_imports，勿放宽）
python -m asr.cli.main --help      # CLI 入口（等价于已安装的 asr 命令）
```

CI（`.github/workflows/ci.yml`）在 Python 3.11–3.13 × Linux/macOS 跑同样的三件套；
发布走 `.github/workflows/pypi.yml`（GitHub release 触发，PyPI trusted publishing）。

CLI 子命令：`init` / `doctor` / `run`（收敛插件）、`assess`（独立评估）、`board-assess` / `board-calibrate` / `board-writeback` / `board-watch` / `board-trend` / `board-mark`（模式 C）。

## 目录结构

- `asr/` — 核心包：`assurance/`（独立评估引擎：assessor/pipeline/grading/watch/writeback 等）、`agents/`（builder/tester/analyzer/opencode_backend）、`controller/convergence.py`（控制论收敛）、`cli/main.py`（CLI 入口）、`adapters/ousheng/`（board 快照 + MCP client）、`config|events|dag|patch|spec|memory/`
- `tests/` — 全部测试（顶层，`asr/tests/` 只是残留 `__pycache__`，无测试）；`board_fixture.py` 与 `fake_mcp_server.py` 是 board 系列测试的关键 fixture
- `docs/MODOU_OuSheng_偏差分析集成方案.md` — 模式 C 设计基线（Final 1.0，设计冻结）
- `AI Software runtime(ASR)技术报告.md` — 技术报告 v2.0（文件名为历史外链，勿改名）
- `.modou/` — 模式 C 运行数据（assessments 档案、calibration、quality-ledger、PMMARK.md 抽检清单），未跟踪，勿提交
- `.runtime/` — 收敛插件沙箱（events/patches/diffs），已 gitignore
- `video/` — 演示视频项目（自带 node_modules，用 `.venv-video`），与主代码无关

## 架构边界（改代码前必读）

- **评估链路只读**：`assess` 不生成测试、不回写项目、不调用 Builder；`test_pass_rate` 是地面真值，证据不足输出 `INCONCLUSIVE`，不得默认 PASS。
- **退出码契约**：`0`=PASS，`1`=DEVIATION/INCONCLUSIVE，`2`=BLOCKED/环境错误，被流水线依赖，勿改。
- **MODOU 永久禁止**（模式 C）：修改既有 WorkItem 状态/负责人/验收要求；绕过官方 MCP 接口手改 board YAML；写回必须以校准达标（`board-calibrate`，3 轮全过）为前置，且 board 必须用可写克隆，**绝不**指向共享生产 checkout。
- **状态标注纪律**：能力断言按 Implemented / Experimental / Planned 标注，禁止把设计写成现状。
- 三种完成状态不得合并：Execution Completed ≠ Deliverable Accepted ≠ Decision Outcome Achieved。

## 环境与坑

- `.env` 含真实 API Key，已 gitignore，绝不提交；模板见 `.env.example`。LLM 调用实际委托给 `opencode` CLI（配置在目标项目的 `opencode.json`）。
- **安全默认值（3.0.0 起）**：`asr init` 不再把真实 api_key 写进 `asr_config.yaml`（一律 `empty`）；opencode 默认不追加 `--auto`（显式 `ASR_OPENCODE_AUTO=1` 才开）；`ASR_OPENCODE_TIMEOUT` 默认 1800s，超时终止子进程并报错。不要在改动中回退这三条。
- pytest 配置在 `pyproject.toml`（`asyncio_mode = "auto"`，testpaths=tests）；仓库无 conftest.py；lint/type 规则也在 pyproject（tests 目录豁免 E741/F841）。
- 两个 venv：`.venv`（主）、`.venv-video`（仅 video/ 用）；`.venv` 由 uv 管理，无 pip 模块，装包用 `uv pip install --python .venv/bin/python`。
- 要求 Python ≥3.11（开发用 3.12）。
- 提交风格：`feat:` / `fix:` / `docs:` 前缀，模式 C 迭代带 P0–P4 阶段号；用户可见行为变更必须同步 CHANGELOG.md。
- 开源文件集（LICENSE / CHANGELOG / CONTRIBUTING / SECURITY / .github/workflows）是产品发布面，改动前读 CONTRIBUTING 的「不可破坏的项目纪律」。

## 敏感区域前置阅读

改动 `asr/assurance/` 或 board 系列命令前，先读 `docs/MODOU_OuSheng_偏差分析集成方案.md`（硬约束四条、耦合面三项）；改动品牌/命名前，先读 README「改名范围」与「术语」两节。
