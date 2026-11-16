<div align="center">

<br>

# 🪵 MoDou（墨斗）· Assurance Runtime

### 执行保证运行时 — 偏差识别、结果验证与纠偏反馈

**当前交付：Software Engineering Domain Plugin（基于控制论的 AI 软件工程收敛能力）**

<br>

从一份 `DESIGN.md` 出发，自动生成完整工程项目代码，<br>
通过 Builder → Tester → Analyzer 闭环迭代，持续收敛直到测试全通过且规格对齐。

<br>

> **能力状态**：上述控制论收敛能力为 **Implemented**（代码 + 单元测试）；
> Assurance Core、Analyzer / Verifier 插件体系与 DEP 契约为 **Planned**，本仓库尚未实现。
> 详见下文「产品定位、改名范围与能力边界」。

<br>

[![Version](https://img.shields.io/badge/version-2.0-4CAF50?style=flat-square)](#)
[![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](#)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](#)
[![Tests](https://img.shields.io/badge/tests-252%20passed-brightgreen?style=flat-square)](#)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-ff69b4?style=flat-square)](#)

<br>

[🚀 快速开始](#-快速开始) · [📖 技术报告](./AI%20Software%20runtime%28ASR%29技术报告.md) · [🌐 在线演示](https://georgewangchn.github.io/AI-Software-Runtime/)

<br>

</div>

---

> ### 💡 核心理念
> **一个弱模型 + 强约束系统 > 一个强模型 + 无约束系统**
>
> 不需要 Claude，不需要 GPT 配额。当前软件工程插件把 LLM 当执行器（不可控、有随机性），用控制论闭环驱动它稳定收敛——任意 OpenAI 兼容接口即可运行。

---

## ✨ 核心特性

当前交付的软件工程领域插件本质是一个**控制论闭环**——把 LLM 当执行器，用反馈驱动它稳定收敛：

```mermaid
flowchart LR
    G["🎯 目标<br/>DESIGN.md"] --> CTRL["🧠 控制器<br/>ASRController"]
    CTRL --> ACT["🔧 执行器<br/>Builder / LLM"]
    ACT --> PLANT["📦 被控对象<br/>代码"]
    PLANT --> SENSE["📡 传感器<br/>Tester + Analyzer"]
    SENSE -.反馈.-> CTRL

    style G fill:#ddf4ff,stroke:#54aeff
    style PLANT fill:#dafbe1,stroke:#4ac26b
    style SENSE fill:#fff8c5,stroke:#d4a72c
```

### 🔒 五重控制论保障

| 控制论要求 | 当前实现机制 |
|:---|:---|
| 🎯 **反馈可靠** | `test_pass_rate` 作为地面真值，Analyzer 语义信号降级为辅助 |
| 🔧 **执行器可约束** | RepairMode 状态机（6 模式）+ Patch 限幅 + Formal Guards 硬约束 |
| ⚖️ **系统稳定** | 振荡检测（三重指纹）+ Circuit Breaker + 退化回滚 + Hysteresis 防抖 |
| 📡 **可观测** | ConvergenceMetrics（15+ 字段）+ 全事件文件化存储 + 可回放 |
| 🎛️ **可控制** | RepairMode 自动切换 + FINAL_VERIFICATION 防假收敛 + Failure Fingerprint |

### 🚀 v2.0 新增优化

| 优化项 | 核心机制 | 效果 |
|:---|:---|:---|
| **增量测试** | 基于 diff + import 依赖图只跑受影响测试 | 反馈延迟 O(全量) → O(子集)，每 3 轮全量校准 |
| **前馈控制** | session reset 后注入结构化 context | Builder 不再"失忆"重复错误修改模式 |
| **双传感器仲裁** | test_pass 与 Analyzer 分歧时触发仲裁 | 防止假收敛逃逸 |
| **自适应限幅** | patch 限幅按 trend / mode 动态调整 | 振荡收紧 1/3，improving 放宽 1.5x |
| **A/B 回滚** | 回归 >15% 立即回滚至 best snapshot | 即时止损，不等 mode 切换 |
| **可观测导出** | metrics_timeline 导出为时序 JSON | 收敛轨迹事后可分析 |

---

## 🏗️ 工作原理

```mermaid
flowchart LR
    D["📋 DESIGN.md<br/>规格文档"] --> RT

    subgraph RT["⚙️ 软件工程收敛插件（当前实现）"]
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
git clone https://github.com/georgewangchn/AI-Software-Runtime.git
cd AI-Software-Runtime

python -m venv .venv
source .venv/bin/activate
pip install -e .
```

> GitHub 仓库名、包名 `asr-runtime` 与 CLI 命令 `asr` 暂沿用原技术标识，本次改名只涉及品牌与产品定位。
> 仓库名迁移需单独评估 GitHub 重定向、已有 clone 地址与外链，见下文「改名范围」。

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

### Step 6 — 运行

```bash
asr run --project my_project --max-iterations 20
```

当前插件会自动：Builder 生成代码 → Tester 验证 → Analyzer 对比规格 → 循环修复直到收敛。

```
ASR 收敛运行时 [直接模式]
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
│   ├── agents/                       # 智能体
│   │   ├── builder.py                #   Builder：代码生成与修复（带会话延续）
│   │   ├── tester.py                 #   Tester：pytest 执行（Sandbox 隔离 + 增量测试）
│   │   ├── analyzer.py               #   Analyzer：diff-only + 结构化偏差分析
│   │   ├── opencode_backend.py       #   opencode CLI 子进程调用后端
│   │   └── llm_tracker.py            #   Token 消耗追踪
│   ├── controller/
│   │   └── convergence.py            # 收敛控制器（~1400行）
│   ├── cli/
│   │   └── main.py                   # CLI 入口（init / doctor / run / compare）
│   ├── config/                       # Pydantic v2 配置模型
│   ├── events/                       # 20 种事件类型 + EventStore
│   ├── dag/                          # Task DAG 并行执行
│   └── runtime.py                    # 运行时入口
├── tests/                            # 单元测试（252 passed）
├── pyproject.toml                    # 包定义 + asr CLI 入口
└── .env.example                      # 环境变量配置模板
```

---

## 🧭 产品定位、改名范围与能力边界

### 产品定位

依据《三 Runtime 统一体系最终方案》Final 1.0，本项目正式名称为 **MoDou（墨斗）**，定位为 **Assurance Runtime（执行保证运行时）**：以决策契约、执行契约、验收标准和证据为依据，独立识别偏差、诊断原因、验证结果、提出纠偏建议并复验。

| 系统 | Runtime | 职责 |
|---|---|---|
| MOSS | Decision Runtime | 决策形成、治理、版本化与反馈接收 |
| OuSheng | Organization Runtime | 任务组织、Agent 协作、执行与证据提交 |
| **MoDou（墨斗）** | **Assurance Runtime** | **独立评估、偏差诊断、结果验证、纠偏反馈** |

三者通过 DEP（Decision Execution Protocol）交换标准化契约与事件。LLM 是语义理解与辅助诊断的能力组件，不是第四个 Runtime，也不拥有最终验证裁决权。

### 当前实现状态

本仓库当前交付的是 **Software Engineering Domain Plugin**，不是 Assurance Core。按方案文档 §11.3 统一标记：

| 能力 | 状态 | 说明 |
|---|---|---|
| Builder / Tester / Analyzer 收敛闭环 | Implemented | 代码 + `tests/` 单元测试 |
| RepairMode、Patch 限幅、Formal Guards、振荡检测、Circuit Breaker、退化回滚、Hysteresis、事件回放 | Implemented | 控制论收敛机制，见技术报告 v2.0 |
| Assurance Core（Contract Registry / Deviation Engine / Verification Engine / Feedback Controller 等） | Planned | 方案文档 §6.2 目标架构，本仓库未实现 |
| Analyzer / Verifier Plugin SDK、Assessment Contract、DEP 契约 | Planned | 方案文档 §6.4–§6.5、§7，本仓库未实现 |
| 与 MOSS / OuSheng 的跨系统集成 | Planned | 方案文档 §10 阶段 5，本仓库未实现 |

Implemented 依据代码与测试标注，不代表已完成生产可用性、覆盖率或性能验证。

### 改名范围（2026-11-15）

本次为**品牌层改名**，不改技术标识。依据方案文档 §11.1「品牌更名不要求立即改动所有技术标识」与 §14「不为更名而删除原 ASR 已有的软件工程收敛能力」。

| 类别 | 处理 | 具体项 |
|---|---|---|
| 已改 | 品牌与产品定位 | 项目名、README 标题与定位、技术报告头部、演示页标题、包描述、脚本与配置注释 |
| 保留 | Python 包与 API | `asr/` 包目录、`from asr ...` import、`ASRConfig` / `ASRController` / `ASRRuntime` / `ASRLogger` 类名 |
| 保留 | CLI 与配置契约 | `asr` 命令、`asr_config.yaml`、`ASR_*` 环境变量、`.asr_sandbox/`、`.runtime/`、包名 `asr-runtime`、egg-info |
| 保留 | 外部引用 | GitHub 仓库名 `AI-Software-Runtime`、`git clone` 地址、GitHub Pages 演示 URL、技术报告文件名 |
| 保留 | 历史数据 | 演示报告中的 `ASR 初版` / `ASR+ 升级版` / `ASR v1.2` / `ASR v1.3` 等当时版本代号 |
| 待迁移 | 用户可见输出 | `asr/cli/main.py` 中 `ASR 收敛运行时` 等输出字符串，需与测试、文档示例同步后再改 |
| 待迁移 | 仓库名 | 需单独评估 GitHub 重定向、已有 clone 地址、CI 与外链，评估通过后由仓库所有者操作 |

保留理由：上述技术标识被 import 路径、已安装包、用户脚本、历史工程数据和外部链接引用；一次性替换属于重构而非改名，会破坏兼容性，且 GitHub 侧重定向无法在本地完成。

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
| `asr init --project <dir>` | 初始化项目（生成 DESIGN.md 模板 + opencode 配置） |
| `asr doctor --project <dir>` | 环境预检（opencode / Python / DESIGN.md / config） |
| `asr run --project <dir> --max-iterations N` | 运行收敛循环 |
| `asr compare --project <dir> --spec <yaml>` | 对比方案效果 |

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
| 🌐 [在线演示](https://georgewangchn.github.io/AI-Software-Runtime/) | 系统效果验证测试报告，对比四种方案的实际生成效果 |

---

<div align="center">

**MIT License**

</div>
