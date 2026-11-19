<div align="center">

<br>

# 🪵 MoDou（墨斗）· Assurance Runtime

### 独立验收运行时 — Tester 确定性验证 + Analyzer 偏差分析

<br>

以既有交付物、`DESIGN.md` 与验收标准为依据，独立识别偏差、验证结果、输出可核验的验收结论。<br>
Builder 只作为**被评估的执行插件**，不参与评估判定。

<br>

> **能力状态**：`asr assess` 独立评估（Verifier + Analyzer + 验收证据登记）为 **Implemented**；
> 软件工程收敛插件（Builder → Tester → Analyzer）为 **Implemented**；
> Assurance Core、Analyzer / Verifier Plugin SDK 与 DEP 契约为 **Planned**。
> 详见下文「产品定位、能力边界与价值主张」。

<br>

[![Version](https://img.shields.io/badge/version-2.0-4CAF50?style=flat-square)](#)
[![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](#)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](#)
[![Tests](https://img.shields.io/badge/tests-259%20passed-brightgreen?style=flat-square)](#)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-ff69b4?style=flat-square)](#)

<br>

[🚀 快速开始](#-快速开始) · [📖 技术报告](./AI%20Software%20runtime%28ASR%29技术报告.md) · [🌐 在线演示](https://georgewangchn.github.io/MoDou/)

<br>

</div>

---

> ### 💡 核心理念
> **被评估方不能自证合格。**
>
> Tester 的 `test_pass_rate` 是确定性地面真值；Analyzer 的语义判断只形成 finding 与建议，不替代事实验证。
> 证据不足时输出 `INCONCLUSIVE`，不默认 `PASS`。

---

## ✨ 核心特性

MoDou 把**评估**从生成闭环中独立出来：Verifier 先给出事实，Analyzer 再给出偏差判断，最后按证据登记验收结论。

```mermaid
flowchart LR
    DELIV["📦 既有交付物<br/>DESIGN.md + 代码 + 测试"] --> VER["🧪 Tester / Verifier<br/>只读 pytest"]
    VER --> ANA["🔍 Analyzer<br/>语义偏差分析"]
    ANA --> ASSESS["📋 Assessment<br/>PASS / DEVIATION<br/>INCONCLUSIVE / BLOCKED"]
    ASSESS -.反馈.-> MOSS["MOSS<br/>决策继续 / 调整 / 终止"]
    BUILDER["🔧 Builder（被评估插件）<br/>不参与评估判定"] -.交付.-> DELIV

    style VER fill:#def7ec,stroke:#0e9f6e
    style ANA fill:#ede9fe,stroke:#7c3aed
    style ASSESS fill:#e8f0fe,stroke:#1a56db
    style BUILDER fill:#f3f4f6,stroke:#6b7280
```

### 🔒 评估与验收机制

| 机制 | 实现 |
|:---|:---|
| 🎯 **事实判定** | `asr assess` 只读运行 pytest，不生成测试、不回写项目；`test_pass_rate` 为地面真值 |
| 🔍 **偏差分析** | 结构化 finding：`OMISSION` / `SEMANTIC_DRIFT` / `CONSTRAINT_VIOLATION` / `EVIDENCE_GAP`，带 severity、confidence、evidence |
| 📋 **验收证据** | 逐条 acceptance 匹配通过的测试 nodeid；无证据即 `EVIDENCE_GAP`，不判 PASS |
| ⚖️ **权责分离** | Analyzer 的推测不覆盖 Verifier 的事实判断；Builder 不参与评估 |
| 🚦 **失败安全** | `PASS` / `DEVIATION` / `INCONCLUSIVE` / `BLOCKED` 四态；超时、证据缺失、验证器故障不得默认 PASS |
| 📄 **结构化输出** | `--json-output` 导出 Assessment（状态、findings、验收证据、pytest 统计） |

### 🚀 软件工程插件的既有能力（保留）

Builder → Tester → Analyzer 收敛链路作为 Software Engineering Domain Plugin 保留，**不参与评估判定**：

| 优化项 | 核心机制 | 效果 |
|:---|:---|:---|
| **增量测试** | 基于 diff + import 依赖图只跑受影响测试 | 反馈延迟 O(全量) → O(子集)，每 3 轮全量校准 |
| **前馈控制** | session reset 后注入结构化 context | Builder 不再"失忆"重复错误修改模式 |
| **双传感器仲裁** | test_pass 与 Analyzer 分歧时触发仲裁 | 防止假收敛逃逸 |
| **自适应限幅** | patch 限幅按 trend / mode 动态调整 | 振荡收紧 1/3，improving 放宽 1.5x |
| **A/B 回滚** | 回归 >15% 立即回滚至 best snapshot | 即时止损，不等 mode 切换 |
| **可观测导出** | metrics_timeline 导出为时序 JSON | 收敛轨迹事后可分析 |

---

## 🏗️ 两种工作模式

### 模式 A — 独立评估（MoDou 核心价值）

```bash
asr assess --project my_project --spec acceptance.yaml --no-analyzer
```

```text
MoDou 独立评估 — /path/to/my_project
状态: PASS
验证: pytest 259/259 passed
验收: 1/1 项有证据
语义分析: skipped
```

评估链路**只读**：不生成测试、不回写项目、不调用 Builder。`--json-output assessment.json` 可导出结构化 Assessment；去掉 `--no-analyzer` 则启用 Analyzer 语义偏差分析（需要 `opencode` CLI）。

### 模式 B — 软件工程收敛插件（保留能力）

以下收敛链路属于 Software Engineering Domain Plugin，评估判定不依赖它：

```mermaid
flowchart LR
    D["📋 DESIGN.md<br/>规格文档"] --> RT

    subgraph RT["⚙️ 软件工程收敛插件"]
        direction LR
        B["🔧 Builder<br/>生成 / 修复"] --> T["🧪 Tester<br/>运行测试"] --> A["🔍 Analyzer<br/>对比规格"]
        A -->|仍有偏差| B
    end

    A -->|规格对齐| C{"✅ 收敛?"}
    C -->|否| B
    C -->|是| O["📦 完整工程项目<br/>测试全通过 · 规格一致"]

    style D fill:#ddf4ff,stroke:#54aeff,stroke-width:1.5px
    style O fill:#dafbe1,stroke:#4ac26b,stroke-width:1.5px
    style C fill:#fff8c5,stroke:#d4a72c
    style RT fill:#f6f8fa,stroke:#d0d7de,stroke-width:1px,color:#24292f
```

> 每轮循环由控制论引擎驱动：实时计算 `test_pass_rate` 趋势、检测振荡、自动切换 RepairMode，确保系统稳定收敛而非无限振荡。



<details>
<summary><b>📋 RepairMode 状态机（点击展开）</b></summary>

<br>

六种修复模式，每种实质改变 Controller 对 Builder 的调用方式，通过 hysteresis 自动切换：

```
INITIAL_GENERATION → TEST_FIX ←─────────────────────┐
                       ↓ (stalled ≥ 2)               │
                   SPEC_COMPLETION                    │
                       ↓ (oscillation ≥ 0.7)         │
                   OSCILLATION_BREAK ──(improving ≥ 2)──┘
                       ↓ (regressing ≥ 2)
                   REGRESSION_RECOVERY ──(improving ≥ 1)──→ TEST_FIX
                       ↓ (tests pass, no analyzer)
                   FINAL_VERIFICATION ──(ALL CLEAR)──→ ✅ 收敛
```

</details>

<details>
<summary><b>🛡️ Formal Guards — 硬约束三件套（点击展开）</b></summary>

<br>

| Guard | 检测内容 | 动作 |
|:---:|---|---|
| 测试删除检测 | Builder 删除了 `test_*.py` 或 `tests/` 下的文件 | 拒绝 patch + 回滚 |
| 语法检查 | 对所有 `.py` 文件执行 `ast.parse()` | 拒绝 patch + 回滚 |
| Bypass 检测 | `except:`、`return expected`、`@pytest.mark.skip`、生产代码中的 `mock` | 拒绝 patch + 回滚 |

</details>

---

## 🚀 快速开始

### 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| 操作系统 | macOS / Linux | — |
| Python | 3.12+ | 推荐 pyenv 管理 |
| Node.js | 18+ | opencode CLI 依赖 |
| opencode CLI | >= 1.15 | LLM 调用后端 |

### Step 1 — 安装 MoDou

```bash
git clone https://github.com/georgewangchn/MoDou.git
cd MoDou

python -m venv .venv
source .venv/bin/activate
pip install -e .
```

> GitHub 仓库名已迁移为 `MoDou`，旧地址由 GitHub 重定向；包名 `asr-runtime` 与 CLI 命令 `asr` 仍沿用原技术标识。

### Step 2 — 安装 opencode CLI

```bash
npm install -g opencode-ai
opencode --version
```

### Step 3 — 初始化项目

```bash
asr init --project my_project
```

自动生成 `DESIGN.md`（规格模板）+ `opencode.json`（LLM 配置模板）+ `asr_config.yaml`。

### Step 4 — 配置 LLM

编辑 `my_project/opencode.json`（opencode 2.x per-repo 配置），填入你的 LLM provider：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "local": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local LLM",
      "options": {
        "baseURL": "http://127.0.0.1:8000/v1",
        "apiKey": "empty"
      },
      "models": {
        "glm-4.7-fp8": {
          "name": "glm-4.7-fp8",
          "limit": { "context": 262144, "output": 32768 }
        }
      }
    }
  },
  "model": "local/glm-4.7-fp8"
}
```

> 支持任意 OpenAI 兼容接口：vLLM / Ollama / OpenAI 官方 / DeepSeek 等。

### Step 5 — 环境检查

```bash
asr doctor --project my_project
```

### Step 6 — 独立评估（推荐）

```bash
asr assess --project my_project --no-analyzer
```

```text
MoDou 独立评估 — /path/to/my_project
状态: INCONCLUSIVE
验证: pytest 259/259 passed
语义分析: skipped
发现:
  [SPEC_INCOMPLETE][MEDIUM][acceptance] 验收基准不完整: goal is required
```

评估退出码：`0` = PASS，`1` = DEVIATION / INCONCLUSIVE，`2` = BLOCKED / 环境错误，便于流水线与 OuSheng 驱动。

测试全绿但没有验收基准时输出 `INCONCLUSIVE`——**执行完成不等于目标达成**。提供 `--spec acceptance.yaml` 后可得到 `PASS` / `DEVIATION` 判定。

### Step 7 — 运行软件工程收敛插件（可选）

```bash
asr run --project my_project --max-iterations 20
```

插件会自动：Builder 生成代码 → Tester 验证 → Analyzer 对比规格 → 循环修复直到收敛。

```
MoDou 软件工程收敛插件 [直接模式]
项目路径: my_project
最大迭代轮次: 20

  [第1轮] 代码生成  错误:0  🔧  | 补丁:0 文件:3 代码行:156 初始生成
  [第2轮] 测试验证  错误:3  ❌  | 通过:12/15 失败:test_foo,test_bar
  [第3轮] 代码修复  错误:3  🔧  | 修复3个失败 pass_rate=0.80 trend=improving
  [第4轮] 测试验证  错误:0  ✅  | 通过:15/15
  [第5轮] 规格分析  错误:0  ✅  | 规格:一致

✅ 已收敛 — 所有测试通过且规格一致
迭代轮次: 5 | 事件数: 23
```

---

## 📁 项目结构

```
asr/
├── asr/                              # 核心代码
│   ├── assurance.py                  # 独立评估引擎：Verifier + Analyzer + 验收证据登记
│   ├── agents/                       # 智能体
│   │   ├── builder.py                #   Builder：被评估的执行插件（收敛链路）
│   │   ├── tester.py                 #   Tester：pytest 执行 + 结果解析
│   │   ├── analyzer.py               #   Analyzer：结构化偏差分析
│   │   ├── opencode_backend.py       #   opencode CLI 子进程调用后端
│   │   └── llm_tracker.py            #   Token 消耗追踪
│   ├── controller/
│   │   └── convergence.py            # 收敛控制器（软件工程插件）
│   ├── cli/
│   │   └── main.py                   # CLI 入口（init / doctor / assess / run）
│   ├── config/                       # Pydantic v2 配置模型
│   ├── events/                       # 事件类型 + EventStore
│   ├── dag/                          # Task DAG 并行执行
│   └── runtime.py                    # 收敛运行时入口
├── tests/                            # 单元测试（259 passed）
│   └── test_assess.py                #   独立评估引擎测试
├── pyproject.toml                    # 包定义 + asr CLI 入口
└── .env.example                      # 环境变量配置模板
```

---

## 🧭 产品定位、能力边界与价值主张

### 产品定位

依据《三 Runtime 统一体系最终方案》Final 1.0，本项目正式名称为 **MoDou（墨斗）**，定位为 **Assurance Runtime（执行保证运行时）**：以决策契约、执行契约、验收标准和证据为依据，独立识别偏差、诊断原因、验证结果、提出纠偏建议并复验。

**价值重心**：MoDou 的价值不来自代码生成，而来自 **Tester 的确定性验证与 Analyzer 的偏差分析**——在既有交付物上独立给出可核验的验收结论。Builder 是被评估的执行插件，其自我声明不构成验收证据。

| 系统 | Runtime | 职责 |
|---|---|---|
| MOSS | Decision Runtime | 决策形成、治理、版本化与反馈接收 |
| OuSheng | Organization Runtime | 任务组织、Agent 协作、执行与证据提交 |
| **MoDou（墨斗）** | **Assurance Runtime** | **Tester 验证 + Analyzer 偏差分析 → 独立验收结论** |

三者通过 DEP（Decision Execution Protocol）交换标准化契约与事件。LLM 是语义理解与辅助诊断的能力组件，不是第四个 Runtime，也不拥有最终验证裁决权。

### 当前实现状态

本仓库交付**独立评估能力**与**软件工程领域插件**两部分，不等同于完整的 Assurance Core。按方案文档 §11.3 统一标记：

| 能力 | 状态 | 说明 |
|---|---|---|
| `asr assess` 独立评估（Verifier / Analyzer / 验收证据登记） | Implemented | 只读 pytest + 结构化 findings + `PASS` / `DEVIATION` / `INCONCLUSIVE` / `BLOCKED` 四态 |
| 软件工程收敛插件（Builder → Tester → Analyzer） | Implemented | 代码 + `tests/` 单元测试；不参与评估判定 |
| RepairMode、Patch 限幅、Formal Guards、振荡检测、Circuit Breaker、退化回滚、Hysteresis、事件回放 | Implemented | 控制论收敛机制，见技术报告 v2.0 |
| Assurance Core（Contract Registry / Deviation Engine / Feedback Controller 等） | Planned | 方案文档 §6.2；`asr assess` 提供最小可用子集，非完整 Core |
| Analyzer / Verifier Plugin SDK、DEP 契约、跨系统集成 | Planned | 方案文档 §6.4–§6.5、§7、§10 阶段 5，本仓库未实现 |
| 模式 C：绳上保证（`asr board-assess`，OuSheng board 两级偏差分析） | Experimental | P1 影子模式已实现并在真实 board（425 items / 15 designs）验证：快照固定、分级路由（T0/T1/T2）、案卷装配、LLM 两级协议、四道门、assessment 存档。写回与校准（P2/P3）未实现 |

Implemented 依据代码与测试标注，不代表已完成生产可用性、覆盖率或性能验证。

### 改名范围（2026-11-15）

本次为**品牌层改名**，不改技术标识。依据方案文档 §11.1「品牌更名不要求立即改动所有技术标识」与 §14「不为更名而删除原 ASR 已有的软件工程收敛能力」。

| 类别 | 处理 | 具体项 |
|---|---|---|
| 已改 | 品牌与产品定位 | 项目名、README 标题与定位、技术报告头部、演示页标题、包描述、脚本与配置注释 |
| 保留 | Python 包与 API | `asr/` 包目录、`from asr ...` import、`ASRConfig` / `ASRController` / `ASRRuntime` / `ASRLogger` 类名 |
| 保留 | CLI 与配置契约 | `asr` 命令、`asr_config.yaml`、`ASR_*` 环境变量、`.asr_sandbox/`、`.runtime/`、包名 `asr-runtime`、egg-info |
| 已迁移 | 仓库与外链 | GitHub 仓库名与 clone 地址改为 `MoDou`，Pages 演示地址改为 `georgewangchn.github.io/MoDou`；技术报告文件名保留 |
| 保留 | 历史数据 | 演示报告中的 `ASR 初版` / `ASR+ 升级版` / `ASR v1.2` / `ASR v1.3` 等当时版本代号 |
| 已迁移 | 用户可见输出 | CLI 输出字符串改为 `MoDou` 品牌；命令名、类名、环境变量等技术标识保留 |
| 已迁移 | 仓库名 | GitHub 仓库改为 `MoDou`，旧地址重定向，clone 与 Pages 地址同步 |
| 已调整 | 价值主张 | 2026-11-16 从「Builder 生成并收敛」转向「Tester / Analyzer 独立评估与验收」；收敛链路保留为插件 |

保留理由：上述技术标识被 import 路径、已安装包、用户脚本和历史工程数据引用；一次性替换属于重构而非改名，会破坏兼容性。仓库改名已由所有者在 GitHub 完成。

### 术语

- **MoDou（墨斗）**：本项目正式名称。方案文档写作全大写 `MODOU`，两者指同一实体，本仓库统一采用 `MoDou（墨斗）`。
- **ASR / AI Software Runtime**：历史项目名；在代码、CLI、环境变量与历史实验数据中作为技术标识继续保留。
- **Assurance Runtime**：目标产品定位；不代表当前仓库已实现方案文档 §6 的全部 Core 能力。

---

## 📚 参考

<details>
<summary><b>CLI 命令速查</b></summary>

| 命令 | 说明 |
|---|---|
| `asr assess --project <dir> [--spec <yaml>] [--no-analyzer]` | **独立评估**：Verifier + Analyzer → `PASS` / `DEVIATION` / `INCONCLUSIVE`，不调用 Builder |
| `asr init --project <dir>` | 初始化项目（生成 DESIGN.md 模板 + opencode 配置） |
| `asr doctor --project <dir>` | 环境预检（opencode / Python / DESIGN.md / config） |
| `asr run --project <dir> --max-iterations N` | 运行软件工程收敛插件（Builder → Tester → Analyzer） |
| `asr assess --project <dir> [--spec y.yaml]` | 只读独立评估（四态 + 退出码契约） |

</details>

<details>
<summary><b>环境变量</b></summary>

| 变量 | 必填 | 说明 |
|------|:---:|------|
| `FEASIBILITY_LLM_API_BASE` | ✅ | OpenAI 兼容接口地址 |
| `FEASIBILITY_LLM_API_KEY` | ✅ | API 密钥（本地部署可填 `empty`） |
| `FEASIBILITY_LLM_MODEL` | ✅ | 模型名称 |
| `FEASIBILITY_LLM_CONTEXT` | | 上下文窗口大小（默认 131072） |
| `ASR_OPENCODE_TIMEOUT` | | opencode 调用超时秒数（默认 24400） |
| `ASR_VERBOSE` | | 设为 `1` 启用详细日志 |

</details>

### 相关文档

| 文档 | 说明 |
|---|---|
| 📖 [技术报告 (v2.0)](./AI%20Software%20runtime%28ASR%29技术报告.md) | 原 ASR 技术报告：系统架构设计、控制论优化体系、端到端验证、DAG 调度、事件总线。文件名为历史外链，保留不改 |
| 💡 原始构想 | 《Supervise-Agent：有监督长任务自动化软件工程系统》—— 项目最初想法：分层裁决 + 多 Agent 协同 + 工程约束。文档已从仓库移除，故不提供链接 |
| 🌐 [在线演示](https://georgewangchn.github.io/MoDou/) | 系统效果验证测试报告，对比四种方案的实际生成效果 |

---

<div align="center">

**MIT License**

</div>
