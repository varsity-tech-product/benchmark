# Marketplace 发布：多任务支持与沙箱安全

> 日期：2026-04-10
> 前置文档：`../architecture/architecture_implementation_plan.md`, `../architecture/dual_protocol_design.md`
> 本文档：解决 MCP Marketplace / Skills Market 发布时的两个关键问题

---

## 一、多任务支持方案

### 1.1 问题

新架构的 HTTP 模式天然支持多 session（每次 `register_session` 指定不同 `task_id`）。
但 MCP Marketplace 场景下，agent 通过 MCP 协议连入后：
- 不知道有哪些 task 可选
- stdio 模式下是单 session，无法动态切换 task

### 1.2 HTTP 模式：`list_tasks` 工具（方案 A）

HTTP 模式已经是多 session 服务，只需补充任务发现能力。

**新增 MCP 工具：**

| 工具 | 阶段 | 参数 | 返回 |
|------|------|------|------|
| `list_tasks` | 任何阶段（全局工具，不绑定 session） | `category?: string` | `{tasks: [{task_id, category, difficulty, description}, ...]}` |

Agent 工作流变为：

```
list_tasks(category="debug")  → 看到 X01-X10
register_session(task_id="X01_ma_offbyone")  → 拿到 session_id
start_session()  → 开始辅导
... 正常交互 ...
request_evaluation()  → 评分
```

`list_tasks` 从 `server/` 内的 task JSON 文件扫描生成，不需要外部依赖。

**对应 REST 端点：**

```
GET /tasks[?category=debug]
```

### 1.3 stdio 模式：启动时指定 task（方案 B）

stdio 是单 session 模式，task 在启动命令中指定：

```bash
python -m server --stdio --task X01_ma_offbyone --docker
```

Marketplace 用户在 agent 配置中体现为每个 task 一个 MCP server 条目：

**Claude Code（`.mcp.json`）：**

```json
{
  "mcpServers": {
    "qtb-x01": {
      "command": "python",
      "args": ["-m", "server", "--stdio", "--task", "X01_ma_offbyone", "--docker"],
      "cwd": "/path/to/quanttutorbench"
    },
    "qtb-x02": {
      "command": "python",
      "args": ["-m", "server", "--stdio", "--task", "X02_lookahead", "--docker"],
      "cwd": "/path/to/quanttutorbench"
    }
  }
}
```

**Codex CLI（`config.toml`）：**

```toml
[mcp_servers.qtb-x01]
command = "python"
args = ["-m", "server", "--stdio", "--task", "X01_ma_offbyone", "--docker"]
```

### 1.4 Skill 快捷入口

提供一个 Skill 作为文档 + 工作流入口，不替代 MCP Server：

```yaml
# .claude/skills/quanttutorbench/SKILL.md
---
name: quanttutorbench
description: >
  Run a QuantTutorBench evaluation task. Connects to the benchmark server
  for interactive quantitative finance tutoring assessment.
disable-model-invocation: true
argument-hint: [task_id]
allowed-tools: Bash(python *) Bash(curl *)
---

## Quick Start
1. Ensure server is running: `python -m server --port 8000 --docker`
2. Call `list_tasks` to see available tasks
3. Call `register_session` with task_id: $ARGUMENTS
4. Call `start_session` to begin the tutoring session
5. Use domain tools (shell_exec, file_read, run_backtest, etc.) to assist the student
6. Call `send_message` to interact with the simulated student
7. Call `request_evaluation` when the session completes
```

### 1.5 推荐组合

| 场景 | 方案 | 用户操作 |
|------|------|---------|
| 云端 / 多 agent 并发评测 | HTTP + `list_tasks` | 连 HTTP endpoint，动态选 task |
| 本地单 task 评测 | stdio + 启动参数 | 配置 MCP server 条目，指定 task |
| 批量评测 | HTTP + client runner | `python -m client --server http://... --group debug` |

---

## 二、沙箱安全与 Marketplace 可用性

### 2.1 问题

Benchmark 暴露 `shell_exec` 工具——agent 可执行任意 shell 命令。
无沙箱时，agent 可能执行破坏性命令（`rm -rf`、网络外传、资源耗尽）。
Docker 提供了必要的隔离，但 Marketplace 用户期望即装即用，Docker 是额外门槛。

### 2.2 分层安全策略

```
Tier 1: 云端托管（最佳体验，零门槛）
  ↓
Tier 2: 本地 Docker（标准体验，需 Docker）
  ↓
Tier 3: 受限本地模式（降级体验，无需 Docker）
```

### 2.3 Tier 1：云端托管

Server 运行在云端（自建或 Glama 托管），Docker 由运维管理。
用户只需连 HTTP endpoint，不感知 Docker 存在。

**配置：**

```json
{
  "mcpServers": {
    "quanttutorbench": {
      "type": "streamable-http",
      "url": "https://qtb.example.com/mcp"
    }
  }
}
```

**优势：** 安全由服务端保证，用户零配置。
**要求：** 需要部署基础设施 + 运维。

### 2.4 Tier 2：本地 Docker

用户本地安装 Docker，server 在 Docker 中创建隔离 container。
这是当前新架构的主力模式。

```bash
pip install quanttutorbench
quanttutorbench --port 8000 --docker
```

**Docker 提供的保护：**

| 威胁 | 隔离机制 |
|------|---------|
| 文件系统破坏 | container 独立文件系统，宿主机不可写 |
| 网络外传 | `network_enabled=False` 时无网络 |
| 资源耗尽 | Docker resource limits（CPU/memory） |
| 进程逃逸 | container namespace 隔离 |

**要求：** 用户本地有 Docker。大多数开发者满足此条件。

### 2.5 Tier 3：受限本地模式

无 Docker 时，通过命令白名单 + 工作目录隔离提供基本安全。

```bash
quanttutorbench --port 8000 --no-docker --restricted
```

**实现：**

```python
# server/core/restricted_sandbox.py

SAFE_EXECUTABLES = {
    "python", "python3",        # 策略代码执行
    "ls", "cat", "head", "tail", "wc",  # 文件查看
    "diff", "grep",             # 文件比较
    "pip", "pip3",              # 包安装（受限）
}

BLOCKED_PATTERNS = [
    r"rm\s+-rf", r"rm\s+-r",   # 递归删除
    r"curl\s", r"wget\s",      # 网络访问
    r">\s*/",                   # 写入根目录
    r"\|.*sh",                  # 管道到 shell
]

def restricted_shell_exec(command: str, workspace: str) -> str:
    """Execute with whitelist + pattern blocking."""
    import shlex, subprocess, re

    # Block dangerous patterns
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command):
            return f"Error: command blocked by security policy"

    # Check executable whitelist
    parts = shlex.split(command)
    executable = parts[0] if parts else ""
    if executable not in SAFE_EXECUTABLES:
        return (f"Error: '{executable}' not allowed in restricted mode. "
                f"Allowed: {', '.join(sorted(SAFE_EXECUTABLES))}")

    # Execute in workspace with timeout
    try:
        result = subprocess.run(
            command, shell=True, cwd=workspace,
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "HOME": workspace}
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "Error: command timed out (60s limit)"
```

**Tier 3 的局限：**

| 工具 | Docker 模式 | Restricted 模式 |
|------|-----------|----------------|
| shell_exec | 完整 | 白名单命令 |
| file_write | 任意路径 | 仅 workspace/ 下 |
| run_backtest | 完整 | 完整（纯 Python） |
| compute_indicator | 完整 | 完整（纯 Python） |
| search_web | 可选 | 禁用 |
| fetch_market_data | 需网络 | 仅本地数据 |

**文档标注：**

> Restricted mode provides basic security without Docker.
> For full functionality and production-grade isolation, use `--docker`.

### 2.6 `__main__.py` 参数设计

```python
parser.add_argument("--docker", action="store_true",
                    help="Run tool execution in Docker containers (recommended)")
parser.add_argument("--restricted", action="store_true",
                    help="Enable restricted mode when running without Docker")

# 启动逻辑
if args.docker:
    sandbox = DockerSandbox()
elif args.restricted:
    sandbox = RestrictedSandbox()
else:
    # 既没有 docker 也没有 restricted → 警告
    log.warning(
        "Running without Docker or restricted mode. "
        "Agent has unrestricted shell access. Use --docker or --restricted."
    )
    sandbox = LocalSandbox()  # 完全不限制，仅开发/测试用
```

### 2.7 Marketplace README 中的安全说明

```markdown
## Security

QuantTutorBench executes agent-generated code in a sandbox.

| Mode | Command | Security | Functionality |
|------|---------|----------|---------------|
| Docker (recommended) | `--docker` | Full isolation | Full |
| Restricted | `--no-docker --restricted` | Command whitelist | Limited |
| Unrestricted | `--no-docker` | None (dev only) | Full |

⚠️ **Never run in unrestricted mode with untrusted agents.**
```

---

## 三、文件改动清单

| 文件 | 改动 |
|------|------|
| `server/__main__.py` | 新增 `--stdio`、`--task`、`--restricted` 参数 |
| `server/api/session_api.py` | `list_tasks` 工具注册（全局，不绑 session） |
| `server/api/http_app.py` | `GET /tasks` REST 端点 |
| `server/core/restricted_sandbox.py` | 新建：受限执行模式 |
| `server/core/registry.py` | sandbox 模式分发（Docker / Restricted / Local） |
| `spec/PROTOCOL.md` | 补充 `list_tasks` 说明 |
| `smithery.yaml` | 新建：marketplace 元数据 |
| `pyproject.toml` | 新建：打包配置 + entry_points |
| `.claude/skills/quanttutorbench/SKILL.md` | 新建：Skill 快捷入口 |
