# Changelog

本项目的全部显著变更记录在此。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [3.0.0] — 2026-11-20

首个打标签的公开版本（此前无 release）。品牌名 MoDou（墨斗），技术标识
`asr-runtime` / `asr` / `asr/` 保留不变。

### 新增

- `asr assess` 独立评估引擎：Verifier（只读 pytest）+ Analyzer（语义偏差分析）
  + 验收证据登记，四态结论 `PASS` / `DEVIATION` / `INCONCLUSIVE` / `BLOCKED`，
  退出码契约 0/1/2。
- 模式 C（绳上保证，Experimental）：`board-assess` / `board-calibrate` /
  `board-writeback` / `board-watch` / `board-trend` / `board-mark`——对 OuSheng
  board 的只读影子偏差分析、注入校准、经官方 MCP 的授权写回与定时巡检。
- 软件工程收敛插件（保留能力）：Builder → Tester → Analyzer，RepairMode 状态机、
  增量测试、自适应 patch 限幅、Formal Guards、振荡检测、事件溯源回放。
- 本版本同步引入的开源产品化基础设施：LICENSE（MIT）、CHANGELOG、
  ruff + mypy 质量门禁、GitHub Actions CI（Python 3.11–3.13 × Linux/macOS）、
  PyPI 发布流水线。

### 安全（行为变更，升级必读）

- `asr init` 不再把 `.env` 中的 `FEASIBILITY_LLM_API_KEY` 明文写入
  `asr_config.yaml`（一律写 `empty` 占位）；`asr doctor` 对配置文件中的明文
  密钥给出告警。真实密钥只应存在于 `.env`（已 gitignore）或 opencode 配置中。
- opencode 子进程默认不再追加 `--auto`：权限决策回到各项目 `opencode.json`
  白名单（`asr init` 生成的模板已收紧 deny 列表）。需要旧行为时显式设置
  `ASR_OPENCODE_AUTO=1`。
- `ASR_OPENCODE_TIMEOUT` 真正生效（此前从未被使用，opencode 挂起会永久阻塞
  整个运行时），默认值从 24400s 收紧为 1800s；超时会终止子进程并抛出明确错误。
- 解耦模式（`--decoupled`）AgentRunner 的轮询异常不再静默吞掉，记入日志。
- board 快照 / 巡检 / 校准的 git 子进程不再硬编码 Homebrew 风格 `PATH`，
  改为继承当前环境并做最小化隔离（屏蔽全局 gitconfig 与交互提示），
  nix/conda/非标准 git 位置与 Linux 发行版环境可正常工作。

### 修复

- `board-assess` 未指定 `--workdir` 时的临时 staging 目录现在会被清理
  （此前每次运行泄漏一个 `modou-assess-*` 目录）。
- Tester 沙箱内 `pip install -r requirements.txt`（LLM 生成的依赖清单）失败
  不再静默，记录警告日志。
- 收敛控制器在未注入 logger 时（测试/库内嵌场景）调用日志不再潜在崩溃
  （`_log` 空值保护）。
- `Event` 基类补齐 `payload` 字段声明，与全部子类及消费方的多态访问一致。
- 类型与 lint 基线清零：ruff（209→0）与 mypy（74→0）全绿，其中顺带修复
  振荡检测中 `list[str]`/`list[float]` 变量复用、回归回滚对空快照的访问等
  真实缺陷。
- 模拟测试（12 场景 × 84 轮，确定性链路）发现并修复三处：
  ① Analyzer 显式开启但降级时曾伪造 `PASS`（现降为 `INCONCLUSIVE`，
  `analyzer_mode` 如实标 `degraded`）；② `board-assess --as-of HEAD` 落档
  符号名而非 sha（现解析固定，快照可追溯）；③ `board-writeback` 对
  report 格式 JSON 曾 KeyError 崩溃（现可读拒绝并指引正确输入）。
- README 徽章与事实对齐：版本 3.0.0、测试数 346（此前为过期的 2.0 / 259）；
  删除与 `pyproject.toml` 重复且混入测试依赖的根目录 `requirements.txt`。
