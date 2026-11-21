# 安全策略（Security Policy）

## 背景

MoDou 会驱动 `opencode` CLI 在你的项目目录中执行 LLM 生成的代码与命令。
尽管默认权限走各项目 `opencode.json` 白名单、Tester/Analyzer 在
`.asr_sandbox/` 沙箱中运行，请仍然只在不含生产凭据的环境中运行收敛插件。

## 报告漏洞

请勿通过公开 issue 报告安全漏洞。发送邮件至 george@senlinpubu.top，
48 小时内会确认收到。请包含：复现步骤、影响评估（涉及密钥落盘 /
任意命令执行 / 沙箱逃逸尤佳）、受影响版本。

## 支持范围

| 版本 | 支持状态 |
|---|---|
| 3.0.x | ✅ |
| < 3.0 | ❌（无 release，直接升级） |

## 已知设计边界

- `asr run`（收敛插件）中的 Builder 直接运行在真实项目目录上——这是该插件
  的工作方式；评估命令（`asr assess`、board 系列）全程只读。
- Tester 会安装 LLM 生成的 `requirements.txt`（沙箱内）以完成测试，安装
  失败会记录警告。如需更严格隔离，请在专用容器中运行收敛插件。
