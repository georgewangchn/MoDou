# 贡献指南（Contributing）

感谢关注 MoDou（墨斗）！

## 开发环境

```bash
git clone https://github.com/georgewangchn/MoDou.git
cd MoDou
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"       # 含 pytest / ruff / mypy / build
```

要求 Python ≥ 3.11；本仓库不提交 `.python-version`（勿引入）。

## 提交前自查

```bash
ruff check .        # 必须零违规
mypy asr            # 必须零错误（宽松基线，勿放宽）
pytest              # 必须全绿（<15s；数量以 CI 为准）
```

CI（`.github/workflows/ci.yml`）在 Python 3.11–3.13 × Linux/macOS 上执行同样的检查。

## 提交规范

- 消息前缀：`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`
- 模式 C（board 系列）相关变更在正文标注阶段号（P0–P4）。
- 用户可见行为变更必须同步更新 `CHANGELOG.md` 与 README（徽章数据以
  `pyproject.toml` 与真实测试数为唯一事实来源）。

## 不可破坏的项目纪律

1. **评估链路只读**：`asr assess` 与 board 系列命令不得写被评估项目或 board；
   写回只能经官方 MCP 接口且以校准达标为前置。
2. **退出码契约**：`0`/`1`/`2` 语义见 `asr/cli/main.py` 头部注释，被下游依赖。
3. **安全默认值**：不引入新的密钥落盘路径；opencode 权限默认走白名单
   （`ASR_OPENCODE_AUTO` 保持默认关闭）。
4. **命名**：品牌 MoDou；技术标识 `asr/`、`ASR*` 类名、`ASR_*` 环境变量、
   CLI `asr` 保持不变（README「改名范围」）。
5. **能力状态标注**：Implemented / Experimental / Planned，禁止把设计写成现状。

## 报告问题

提 issue 时请附：`asr doctor --project <dir>` 输出、CLI 命令与退出码、
`.runtime/logs/` 相关片段（**先确认不含密钥**）。
