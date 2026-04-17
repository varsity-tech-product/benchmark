# QuantTutorBench v5.0 前端可视化执行计划书

> 日期：2026-04-13
> 目标：为新 Client-Server 架构搭建可视化前端，结果展示与 legacy 版本一致
> 执行者：Codex
> 版本：v4（2026-04-16 同步本地实现状态与新架构变化）

---

## 0. 总体原则

- **视觉复刻，数据重建**：UI 延续 legacy 暖色主题和三栏布局，数据层围绕 session 重组
- **Results-first**：第一阶段只做结果浏览，不做 Dashboard、不做 live monitoring
- **Merged Read Model**：前端消费的数据来自 server run_state + client trace + task metadata + evaluation 四个来源的合并
- **UI API 与 Protocol API 分离**：新增 `/ui/*` 只读展示接口，不污染 `/session/*` 协议接口

### 首轮交付范围

- Results 列表（filter bar + session cards）
- Results 详情（三栏布局：Info + Conversation + Tools）
- Score / Cost / Trace Report 弹窗
- Tasks 浏览页

### 当前实现状态（2026-04-16）

首轮交付范围已经落地在新的隔离路径 `bench/server/web/`，并且已经额外完成以下扩展：

- 左侧 `Info`、中间 `Conversation`、右侧 `Tools` 三栏详情页；状态栏固定在顶部，左右栏支持 legacy 风格的侧边展开/收起。
- `send_message` 已从普通 domain tool 中拆出，作为协议通信事件在 Conversation 中独立展示；右侧 `Tools` 仅展示真正的 domain tools。
- 右上角 `Workspace` Explorer 已实现，支持列表、搜索、类型统计、markdown/json/csv/code/text/image 预览，并保留 raw/download 入口。
- `ResultIndexer` 已合并 server `run_state`、client `client_trace`、task/persona metadata、evaluation reports，并对缺失 client trace 做降级。
- `Run` 页面已拆分为 `Agent Test` / `Human Test` 入口：Human 继续使用 REST session harness；Agent 只展示真实 MCP client runner 流程和后端 job launcher 缺口，不把自动化 agent 测试伪装成人工聊天。
- `bench/tests/test_server_web_ui.py` 已覆盖 indexer、UI routes、workspace preview、静态挂载和 `send_message` 拆分。

**不包含**（后续阶段）：
- Dashboard 统计页
- Evaluate / Re-evaluate 触发
- Live session monitoring

---

## 1. 文件结构

```
bench/server/web/
├── __init__.py
├── ui_app.py                          # Starlette Route 定义: /ui/* 端点
├── ui_indexer.py                      # 结果扫描 + merged read model 聚合
├── templates/
│   └── index.html                     # 单页应用 HTML
└── static/
    ├── css/
    │   └── styles.css                 # 从 legacy 复制，微调
    └── js/
        ├── app.js                     # 新写：路由 + 状态 + API 调用
        ├── chat.js                    # 从 legacy 复制，仅保留 replay 功能
        ├── render.js                  # 从 legacy 原样复制
        └── tools.js                   # 从 legacy 复制，微调
```

---

## 2. Starlette 接线方案

### 2.1 当前真实结构

`bench/server/api/http_app.py` 的 `create_app()` (line 654) 返回 `_ServerApp`，它是一个自定义 ASGI app，不是 FastAPI 实例：

```python
class _ServerApp:
    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._rest_app(scope, receive, send)
        elif scope["type"] == "http" and scope.get("path", "") in ("/mcp", "/mcp/"):
            await self._manager.handle_mcp_request(scope, receive, send)
        else:
            await self._rest_app(scope, receive, send)
```

REST 路由通过 `Starlette(routes=[Route(...), ...])` 注册（line 673-684）。

### 2.2 挂载方式

在 `create_app()` 中修改 `rest_routes` 列表，追加 UI 路由和静态文件挂载：

```python
# --- 在 create_app() 中, rest_routes 定义之后 ---
from starlette.staticfiles import StaticFiles
from starlette.routing import Mount, Route
from starlette.responses import FileResponse
from server.web.ui_app import ui_routes  # 返回 list[Route]

# UI 页面路由
web_dir = Path(__file__).resolve().parent.parent / "web"
async def serve_index(request):
    return FileResponse(str(web_dir / "templates" / "index.html"),
                        headers={"Cache-Control": "no-cache"})

all_routes = [
    Route("/", serve_index),                           # 首页
    *ui_routes(manager),                               # /ui/* 路由
    *rest_routes,                                      # /session/* 路由
    Mount("/static", app=StaticFiles(directory=str(web_dir / "static")), name="static"),
]

rest_app = Starlette(routes=all_routes, lifespan=lifespan)
```

**注意**：`Route("/")` 必须在 `rest_routes` 之前（Starlette 按顺序匹配）。`Mount("/static")` 放最后。

### 2.3 `ui_app.py` 的接口

```python
# bench/server/web/ui_app.py
from starlette.routing import Route
from starlette.responses import JSONResponse, FileResponse

def ui_routes(manager) -> list[Route]:
    """返回 UI 路由列表，挂载到 Starlette app"""
    indexer = ResultIndexer(...)

    async def list_results(request): ...
    async def get_detail(request): ...
    async def get_file(request): ...
    async def list_tasks(request): ...

    return [
        Route("/ui/tasks", list_tasks),
        Route("/ui/results", list_results),
        Route("/ui/results/{session_id}", get_detail),
        Route("/ui/results/{session_id}/workspace", get_workspace),
        Route("/ui/results/{session_id}/workspace/preview/{path:path}", get_workspace_preview),
        Route("/ui/results/{session_id}/files/{path:path}", get_file),
    ]
```

---

## 3. 后端：UI API 层

### 3.1 字段来源表

所有字段的精确数据来源、fallback 和缺失处理：

| 字段 | Primary Source | Fallback Source | 缺失时前端显示 |
|------|---------------|-----------------|---------------|
| `session_id` | run_state.json → `session_id` | 目录名 | （必须存在） |
| `task_id` | run_state.json → `task_id` | 父目录名 | （必须存在） |
| `category` | tasks/layer2/{cat}/{task_id}.json → `category` | 从 task_id 前缀推断 | "unknown" |
| `difficulty` | tasks/layer2/{cat}/{task_id}.json → `difficulty` | — | "unknown" |
| `persona_id` | run_state.json → `persona_id` | — | "unknown" |
| `duration_seconds` | run_state.json → `duration_seconds` | — | 0 |
| `turn_count` | `len([t for t in conversation if t["role"]=="assistant"])` | — | 0 |
| `tool_count` | `len(domain_tool_logs)` after filtering out `send_message` | — | 0 |
| `send_message_count` | `len(send_message_events)` split from raw `tool_logs` | — | 0 |
| `step_count` | run_state.json → `step_count`（substantive steps，由 `result_writer` 排除 non-substantive tools） | `len(raw_tool_logs)` | 0 |
| `evaluation_status` | run_state.json → `evaluation_status` | `_resolve_latest_eval_dir()` 存在则 "completed" | "pending" |
| `overall_score` | `_resolve_latest_eval_dir()` / eval_meta.json → `overall_score` | — | `null`（前端显示 "—"） |
| `model` | client_trace.json → `agent_cost.model` | — | `null`（前端显示 "Unknown"） |
| `agent_name` | 从 `model` 前缀提取（如 `anthropic/claude...` → `anthropic`） | — | `null`（前端显示 "Unknown"） |
| `timestamp` | client_trace.json → `timestamp` | run_state.json 文件的 `mtime` | 文件修改时间 |
| `has_client_trace` | `client_trace.json` 文件存在 | — | `false` |
| `has_content_blocks` | client_trace 存在且 `content_blocks` 非空 | — | `false` |
| `has_agent_files` | `agent_files/` 目录存在且非空 | — | `false` |
| `scores_md` | `_resolve_latest_eval_dir()` / scores.md | — | `null`（前端隐藏 Score Report 按钮） |
| `cost_md` | `_resolve_latest_eval_dir()` / cost.md | — | `null`（前端隐藏 Cost Report 按钮） |
| `trace_md` | `_resolve_latest_eval_dir()` / trace.md | — | `null`（前端隐藏 Trace Report 按钮） |

#### `tool_logs` 口径

新架构中 `send_message` 是 agent 可见、通过 proxy 记录的通信工具，因此会出现在原始 `run_state["tool_logs"]` 中。但 UI 展示时必须拆分：

- `all_tool_logs`：原始日志，保留审计/调试完整性。
- `tool_logs`：过滤掉 `send_message` 后的 domain tool 日志，用于右侧 Tools 面板和 `tool_count`。
- `send_message_events`：从 `send_message` 日志解析出的通信事件，用于 Conversation 中的独立协议卡片。
- `send_message_count`：通信事件数量，用于 Info 面板和结果摘要。

不要再使用 `len(run_state["tool_logs"])` 作为右侧工具数量，否则协议通信会混入 domain tools。

#### `_resolve_latest_eval_dir()` 实现

`evaluations/latest` symlink 是 best-effort 的（`eval_writer.py:136` 失败时仅 warning）。必须有 fallback：

```python
def _resolve_latest_eval_dir(result_dir: Path) -> Path | None:
    """返回最新评估目录。优先 latest symlink，fallback 到时间最新的 eval_* 目录。"""
    eval_root = result_dir / "evaluations"
    if not eval_root.exists():
        return None

    # 尝试 latest symlink
    latest = eval_root / "latest"
    if latest.exists() and latest.is_dir():
        return latest.resolve()

    # Fallback: 时间最新的 eval_* 目录
    eval_dirs = sorted(
        [d for d in eval_root.iterdir() if d.is_dir() and d.name.startswith("eval_")],
        key=lambda d: d.name,
        reverse=True,
    )
    return eval_dirs[0] if eval_dirs else None
```

所有需要读取 eval_meta.json / scores.md / cost.md / trace.md 的地方，都通过此函数定位目录，不直接硬编码 `evaluations/latest/`。

### 3.2 `turn_count` 定义

**产品口径**：`turn_count` = assistant turns 数量（导师发言次数）。

```python
turn_count = sum(1 for t in conversation if t["role"] == "assistant")
```

不使用 `len(conversation) // 2`，因为对话不保证严格二元配对。

### 3.3 `GET /ui/tasks`

扫描 `bench/tasks/layer2/` 和 `bench/personas/`。实现参考 legacy `web/api/tasks.py` 的 `list_tasks()`。

### 3.4 `GET /ui/results`

**结果目录结构（v5.0）**：

```
results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:8]}/
    run_state.json    ← 含 timestamp 字段
    run_state.md
    agent_files/
    evaluations/
```

例：
```
results/server/X01_ma_offbyone/
├── advanced_quant/
│   ├── 20260413_103000_ced929d2/
│   └── 20260413_150000_a1b2c3d4/
└── beginner_no_finance/
    └── 20260413_110000_e5f6a7b8/
```

**向后兼容**：扫描逻辑同时支持旧 `{task_id}/{session_id}/` 和新 `{task_id}/{persona_id}/{ts}_{short_id}/` 两种布局。识别方法：目录下直接有 `run_state.json` 为旧布局，否则向下一层查找。

扫描逻辑：
```python
def _iter_result_dirs(server_results_dir):
    for task_dir in sorted(server_results_dir.iterdir()):
        if not task_dir.is_dir(): continue
        for child in sorted(task_dir.iterdir()):
            if not child.is_dir(): continue
            if (child / "run_state.json").exists():
                yield child  # Old layout: {task_id}/{session_id}/
            else:
                # New layout: {task_id}/{persona_id}/{ts}_{short_id}/
                for run_dir in sorted(child.iterdir()):
                    if run_dir.is_dir() and (run_dir / "run_state.json").exists():
                        yield run_dir
```

**可选查询参数**：`?category=`, `?task_id=`, `?eval_status=`

**排序**：按 `timestamp` 降序

### 3.5 `GET /ui/results/{session_id}`

返回 merged detail 对象。

#### Conversation Merge 规则

```python
def merge_conversation(server_conv: list[dict], client_trace: dict | None) -> list[dict]:
    """以 server conversation 为权威主体，从 client content_blocks 按 turn 注入。"""
    if not client_trace:
        return [{"role": t["role"], "content": t["content"], "content_blocks": None}
                for t in server_conv]

    cb_by_turn = client_trace.get("content_blocks", {})
    merged = []
    assistant_idx = 0

    for turn in server_conv:
        entry = {"role": turn["role"], "content": turn["content"], "content_blocks": None}
        if turn["role"] == "assistant":
            blocks = cb_by_turn.get(str(assistant_idx))
            if blocks:
                entry["content_blocks"] = blocks
            assistant_idx += 1
        merged.append(entry)

    return merged
```

**注意**：
- client_trace 的 `content_blocks` key 是字符串化的 integer
- merge 失败时静默降级为纯文本（content_blocks=None），不抛异常

#### Eval History 组装

```python
def load_eval_history(result_dir: Path) -> list[dict]:
    eval_dir = result_dir / "evaluations"
    if not eval_dir.exists():
        return []
    history = []
    for sub in sorted(eval_dir.iterdir()):
        if not sub.name.startswith("eval_") or sub.is_symlink():
            continue
        meta_path = sub / "eval_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            history.append(meta)
    return sorted(history, key=lambda m: m.get("timestamp", ""), reverse=True)
```

#### Response Schema

```json
{
  "session_id": "abc123",
  "task_id": "X01_ma_offbyone",
  "category": "debug",
  "persona_id": "beginner_no_finance",
  "duration_seconds": 245.3,
  "turn_count": 8,
  "tool_count": 15,
  "step_count": 12,
  "model": "claude-sonnet-4-6",
  "agent_name": "anthropic",
  "evaluation_status": "completed",

  "conversation": [
    {"role": "user", "content": "...", "content_blocks": null},
    {"role": "assistant", "content": "...", "content_blocks": [{...}, ...]}
  ],

  "tool_logs": [...],               // domain tools only; excludes send_message
  "all_tool_logs": [...],           // raw run_state tool_logs
  "send_message_events": [...],     // protocol communication events
  "send_message_count": 3,
  "workspace_files": [...],
  "distractor_names": [...],

  "scores_md": "# Score Report\n...",
  "cost_md": "# Cost Report\n...",
  "trace_md": "# Trace Report\n...",

  "eval_history": [...],
  "agent_cost": {...}
}
```

**注意**：detail response 也直接返回 `turn_count` / `tool_count` / `send_message_count` / `step_count`。不要让 `buildInfoPanel(data)` 在前端重复推导这些值，避免列表页与详情页口径不一致。

### 3.6 `GET /ui/results/{session_id}/files/{path:path}`

支持嵌套路径（如 `data/prices.csv`）。

```python
async def get_file(request):
    session_id = request.path_params["session_id"]
    file_path = request.path_params["path"]

    task_id = indexer.get_task_id(session_id)
    if not task_id:
        return JSONResponse({"error": "Not found"}, status_code=404)

    agent_files_root = (indexer.find_result_dir(session_id) / "agent_files").resolve()
    full_path = (agent_files_root / file_path).resolve()

    # 路径遍历保护：resolve() 后确认仍在 agent_files 根目录下
    try:
        full_path.relative_to(agent_files_root)
    except ValueError:
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    if not full_path.exists() or not full_path.is_file():
        return JSONResponse({"error": "Not found"}, status_code=404)

    return FileResponse(str(full_path))
```

**注意**：不要用 `if ".." in file_path` 做字符串检查——这能挡住常见输入但不够严格（如 symlink / prefix confusion）。必须用 `resolve()` + `relative_to()`（或 `os.path.commonpath()`）确认最终路径在 `agent_files/` 内。

### 3.6.1 `GET /ui/results/{session_id}/workspace`

当前实现已经新增 Workspace Explorer 数据端点。它不是 raw file 下载接口，而是为右上角 Workspace 弹窗提供文件索引：

```json
{
  "session_id": "abc123",
  "file_count": 3,
  "top_extensions": [".py", ".csv", ".png"],
  "files": [
    {
      "path": "reports/chart.png",
      "name": "chart.png",
      "extension": ".png",
      "size_bytes": 12345,
      "mime_type": "image/png",
      "kind": "image",
      "raw_url": "/ui/results/abc123/files/reports/chart.png"
    }
  ]
}
```

实现要点：

- 优先使用 `run_state["workspace_files"]`，缺失时扫描 `agent_files/`。
- `kind` 当前支持 `image` / `markdown` / `json` / `csv` / `code` / `text` / `binary`。
- raw 文件仍通过 `/ui/results/{session_id}/files/{path:path}` 读取。

### 3.6.2 `GET /ui/results/{session_id}/workspace/preview/{path:path}`

Workspace 预览端点用于弹窗内展示文件内容，避免点击文件时打开新的 HTML 页面。

当前实现：

- 图片：返回 metadata + `raw_url`，前端 inline `<img>` 展示。
- Markdown：返回 `content_text`，前端走 `QTB.renderMarkdown()`。
- JSON：小文件 pretty print；过大文件按文本截断。
- CSV/TSV：返回 `columns` + `rows`，前端表格展示，限制前 100 行、24 列。
- Code/Text：返回 `content_text`，前端 `<pre><code>` 展示。
- Binary：返回不可预览说明，只保留 raw/download。

该端点与 raw file 一样必须使用 `resolve()` + `relative_to()` 做路径逃逸保护。

### 3.7 索引缓存策略

**第一版：每次请求全量扫描。** 简单、稳定、无一致性风险。

理由：结果目录通常不超过几百个 session，扫描开销在毫秒级。如果未来需要优化，可改为内存缓存 + mtime 检查。

```python
class ResultIndexer:
    def __init__(self, server_results_dir, client_results_dir, tasks_dir):
        self._server_dir = Path(server_results_dir)
        self._client_dir = Path(client_results_dir)
        self._tasks_dir = Path(tasks_dir)
        self._task_meta_cache = {}  # task_id → {category, difficulty, ...}

    def _load_task_meta(self):
        """一次性加载所有任务元数据（启动时调用，任务定义不变）"""
        ...

    def list_results(self, **filters) -> list[dict]:
        """每次请求扫描 results/server/，组装 summary 列表"""
        ...

    def get_detail(self, session_id: str) -> dict | None:
        """扫描定位 session_id，加载 + merge + 返回"""
        ...

    def find_result_dir(self, session_id: str) -> Path | None:
        """定位 session_id 的结果目录（兼容新旧布局）"""
        short_id = session_id[:8]
        for result_dir in self._iter_result_dirs():
            if result_dir.name == session_id:
                return result_dir
            if result_dir.name.endswith(f"_{short_id}"):
                return result_dir
        return None
```

### 3.8 JSON Sanitize

```python
import math

def sanitize_for_json(obj):
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    return obj
```

在 `list_results` 和 `get_detail` 返回 JSON 前调用。

### 3.9 新 Client-Server 架构更新

当前本地代码相较最初执行计划有几处架构变化，后续实现必须以这些变化为准：

1. `send_message` 是 agent 可见工具，不是 server 内部私有调用。`SessionState.get_visible_tools()` 在 `IN_SESSION` 阶段返回 `[send_message, get_background] + domain_tools`。
2. client runner 只过滤 `register_session` / `start_session` / `request_evaluation` / `get_results` / `get_scores`，因此 agent 可以在一次 `adapter.generate_response()` 内自主调用 `send_message`、`get_background` 和 domain tools。
3. `send_message` 通过 proxy 调用并进入 raw `tool_logs`，以保证审计完整；UI/报告层负责把它从 domain tools 中拆出。
4. `send_message` 已支持 `attachments`：最多 3 个 workspace 文件；文本会截断读取，图片会进入 file ledger；学生只看到通过 `send_message` 发送的文字和附件，不会看到 agent 普通文本输出、工具调用或 raw command output。
5. `start_session` 返回 `background + student_message`。client 把 background 注入本地 agent context，但 server 保存的正式 conversation 从学生 opening 开始，不包含这个 synthetic background turn。
6. server 保存结果时可能把 `run_state.session_id` 写成 `{uuid}_{task_prefix}`，而 client trace 目录通常仍按原始 `{uuid}` 保存。UI 合并 client trace 时必须兼容两种 session id 口径，至少要支持先按完整 id、再按去除 `_{A00}` 后缀的 id 查找。
7. `client/trace_writer.py` 已增加 `content_blocks_mode`，并会在 whole-session capture 场景下按成功的 `send_message` 工具调用切分 content blocks。UI merge 层应优先消费已切好的 per-turn blocks；遇到旧 trace 或无法对齐的 trace 时降级为纯文本，不能错位渲染。

---

## 4. content_blocks schema 规范

### 4.1 thinking block 的字段名

client 落盘时 thinking block 使用 `"text"` 字段（见 `client/adapters/anthropic_adapter.py`）：

```json
{"type": "thinking", "text": "The student seems to..."}
```

legacy `chat.js` (line 266) 也按 `block.text` 读取：

```javascript
var thinkEl = createThinkingBlockEl(block.text || '');
```

**规范**：后端 merge 层不做 normalize，保持原始 `"text"` 字段。前端统一按 `block.text` 读取 thinking 内容。

### 4.2 tool_use block

```json
{"type": "tool_use", "name": "shell_exec", "input": {"command": "ls"}}
```

也可能是 `"args"` 代替 `"input"`。前端需要兼容两者：
```javascript
var args = block.input || block.args || {};
```

### 4.3 tool_result block

```json
{"type": "tool_result", "tool_use_id": "toolu_xxx", "content": "result text"}
```

`content` 可以是 string 或 array。前端需要处理两种情况。

---

## 5. 前端：Legacy 组件复用对照表

### 5.1 CSS Selector 对照

| Legacy Selector | 行号 | 用途 | 新前端 |
|----------------|------|------|--------|
| `#nav` | 95 | 导航栏容器 | 直接复用 |
| `.nav-brand` | 107 | Logo 区域 | 直接复用 |
| `.nav-links` | 120 | 导航链接列表 | 直接复用 |
| `.nav-link` | 122 | 单个导航链接 | 直接复用 |
| `.page-run` | 259 | 全屏容器 | 重命名为 `.page-detail` |
| `.run-layout` | 265 | flex row 布局 | 重命名为 `.detail-layout` |
| `.run-config` | 269 | 左侧配置栏 (240px) | 重命名为 `.info-panel`，去掉 form 相关子样式 |
| `.run-main` | 670 | 中+右 flex 容器 | 重命名为 `.detail-main` |
| `.run-chat-panel` | 672 | 中栏 (flex:2) | 重命名为 `.chat-panel` |
| `.chat-area` | 693 | 消息列表 | 直接复用 |
| `.run-tool-panel` | 283 | 右栏 (flex:1) | 重命名为 `.tool-panel` |
| `.tool-area` | — | 工具列表 | 直接复用 |
| `.msg` + `.tutor` / `.student` | 703-807 | 消息气泡 | 直接复用 |
| `.bubble` | 750 | 气泡内容 | 直接复用 |
| `.tool-call` | 849 | 工具卡片 | 直接复用 |
| `.tool-header` / `.tool-name` / `.tool-status` / `.tool-duration` | 858-887 | 工具卡片内部 | 直接复用 |
| `.cb-container` | 1722 | content_blocks 容器 | 直接复用 |
| `.cb-thinking` / `.cb-thinking-header` / `.cb-thinking-body` | 1730-1784 | thinking 折叠块 | 直接复用 |
| `.cb-tool` / `.cb-tool-header` / `.cb-tool-name` / `.cb-tool-status` | 1787-1842 | 内联工具卡片 | 直接复用 |
| `.cb-tool-images` / `.cb-tool-img` | 1841-1842 | 内联图片 | 直接复用 |
| `.modal-overlay` | 1390 | 弹窗遮罩 | 直接复用 |
| `.modal-content` | 1400 | 弹窗主体 | 直接复用 |
| `.modal-header` / `.modal-title` / `.modal-close` | 1415-1424 | 弹窗头部 | 直接复用 |
| `.modal-body` | 1437 | 弹窗内容（需加滚动） | 复用 + 加 `overflow-y: auto; max-height: calc(100vh - 120px)` |
| `.badge` | — | 状态标签 | 直接复用 |
| `.btn` / `.btn-primary` | — | 按钮 | 直接复用 |

**需要删除的 CSS**（不复制到新前端）：
- `.run-select-*`（运行类型选择）
- `.group-*`（分组运行）
- `.thinking-dots` / `.responding-dots`（live 动画）
- `.dot` / `.dot-connected`（SSE 连接状态指示器）
- `.eval-*` 相关的进度条样式

### 5.2 JS Export 对照

#### chat.js — Legacy exports (line 491-500)

| Export | 新前端 | 处理方式 |
|--------|--------|---------|
| `QTB.addChatMessage` | 保留 | replay 和 detail 都需要 |
| `QTB.clearChat` | 保留 | 切换 detail 时清空 |
| `QTB.buildConversationReplay` | **保留并修改** | 见下方 |
| `QTB.showThinking` | 删除 | live-only |
| `QTB.updateThinking` | 删除 | live-only |
| `QTB.hideThinking` | 删除 | live-only |
| `QTB.showResponding` | 删除 | live-only |
| `QTB.hideResponding` | 删除 | live-only |

**`buildConversationReplay` 修改**：

Legacy 版本（line 452-487）从 `turn.content_blocks` 取 blocks。新前端 merged conversation 中每个 turn 已经有 `content_blocks` 字段。需要确认 legacy 代码读取路径一致——它已经是 `turn.content_blocks`（line 470），无需修改。

#### tools.js — Legacy exports (line 289-295)

| Export | 新前端 | 处理方式 |
|--------|--------|---------|
| `QTB.addToolStart` | 删除 | live-only |
| `QTB.updateToolResult` | 删除 | live-only |
| `QTB.clearTools` | 保留 | 切换 detail 时清空 |
| `QTB.buildToolReplay` | 保留 | 核心回放函数 |

#### Modal 依赖

`showToolModal()` (tools.js line 46) 调用 `window._qtbShowModal(title, html)`。

新前端的 `app.js` 需要定义这个全局函数：

```javascript
window._qtbShowModal = function(title, html) {
    var overlay = document.getElementById('modal-overlay');
    overlay.querySelector('.modal-title').textContent = title;
    overlay.querySelector('.modal-body').innerHTML = html;
    overlay.style.display = '';
};
```

并绑定关闭事件（点击 close 按钮 / 按 Escape / 点击 overlay）。

#### render.js — Legacy exports (line 95-97)

| Export | 新前端 |
|--------|--------|
| `QTB.renderMarkdown` | 原样复用 |
| `QTB.escapeHtml` | 原样复用 |

### 5.3 图片 URL 重写

Legacy 中图片 URL 的所有位置和修改方式：

| 文件 | 行号 | 当前代码 | 改为 |
|------|------|---------|------|
| tools.js | 81 | `/api/files/live/` + fname | 不改源码——由 `rewriteImages()` 兼容 `/api/files/live/` 前缀 |
| tools.js | 201 | `/api/files/live/` + fname | 同上 |
| tools.js | 273 | 裸 fname（replay 模式） | 不改——由 app.js rewrite |
| chat.js | 156 | 裸 fname | 不改——由 app.js rewrite |
| chat.js | 221 | `/api/files/live/` + fname | 改为 `/ui/results/{sessionId}/files/` + fname |

**app.js 中的 rewrite 函数**（替代 legacy 的 `rewriteImages`）：

```javascript
function rewriteImages(container, sessionId) {
    var imgs = container.querySelectorAll('img');
    imgs.forEach(function(img) {
        var src = img.getAttribute('src') || '';
        // 跳过已经是正确 URL 的
        if (src.startsWith('http') || src.startsWith('/ui/') || src.startsWith('data:')) return;

        // 兼容两类 legacy/replay 路径：
        // 1. /workspace/data/chart.png → data/chart.png
        // 2. /api/files/live/chart.png → chart.png
        // 3. chart.png → chart.png
        var relPath = src;
        if (relPath.startsWith('/workspace/')) {
            relPath = relPath.slice('/workspace/'.length);
        } else if (relPath.startsWith('/api/files/live/')) {
            relPath = relPath.slice('/api/files/live/'.length);
        }

        // 按 path segment 分别编码
        var encoded = relPath.split('/').map(encodeURIComponent).join('/');
        img.src = '/ui/results/' + sessionId + '/files/' + encoded;
    });
}
```

**重要**：不要使用 `src.split('/').pop()` 只保留文件名——这会丢失子目录路径信息，导致不同目录下的同名文件冲突。文件服务端点 `{path:path}` 支持嵌套路径，前端必须保持一致。

在 `showResultDetail` 渲染完 chatEl 和 toolsEl 后调用：
```javascript
rewriteImages(chatEl, sessionId);
rewriteImages(toolsEl, sessionId);
```

**Modal 中的图片**：`showToolModal` 中的图片 URL 也需要 rewrite。最简单的做法是在 modal 显示后再调用 `rewriteImages(modalBody, sessionId)`。需要将当前 sessionId 存储在全局变量中。

---

## 6. 前端：新写的 `app.js`

### 6.1 架构

```javascript
(function() {
    var _sessionId = null;  // 当前详情页的 session_id（图片 rewrite 用）

    function api(path) {
        return fetch('/ui' + path).then(function(r) { return r.json(); });
    }

    // Modal
    window._qtbShowModal = function(title, html) {
        var overlay = document.getElementById('modal-overlay');
        overlay.querySelector('.modal-title').textContent = title;
        var body = overlay.querySelector('.modal-body');
        body.innerHTML = html;
        overlay.style.display = '';
        // Rewrite images in modal
        if (_sessionId) rewriteImages(body, _sessionId);
    };

    // 路由
    function onRouteChange() {
        var hash = location.hash.slice(1) || '/results';
        if (hash === '/' || hash === '/results') showResultsList();
        else if (hash.startsWith('/results/')) showResultDetail(hash.split('/results/')[1]);
        else if (hash === '/tasks') showTasks();
    }

    window.addEventListener('hashchange', onRouteChange);
    window.addEventListener('load', onRouteChange);
})();
```

### 6.2 路由表

```
#/                               → 重定向到 #/results
#/results                        → 结果列表
#/results/{session_id}           → 结果详情
#/tasks                          → 任务列表
```

### 6.3 Results 列表页

```javascript
function showResultsList() {
    api('/results').then(function(data) {
        var app = document.getElementById('app');
        app.innerHTML = '';
        // 渲染 filter bar + session cards
        // 每个 card 点击跳转到 #/results/{session_id}
    });
}
```

Filter bar 包含：
- Category 下拉
- Eval Status 下拉
- Model 下拉（从结果中动态提取去重）
- 搜索框（按 task_id / session_id 模糊匹配，前端过滤）

### 6.4 Results 详情页

```javascript
function showResultDetail(sessionId) {
    _sessionId = sessionId;
    api('/results/' + sessionId).then(function(data) {
        var app = document.getElementById('app');
        app.innerHTML = ''; // 构建三栏 DOM

        // 左栏 Info
        var infoEl = buildInfoPanel(data);

        // 中栏 Conversation
        var chatEl = document.createElement('div');
        chatEl.className = 'chat-area';
        QTB.buildConversationReplay(chatEl, data.conversation, data.tool_logs);
        rewriteImages(chatEl, sessionId);

        // 右栏 Tools
        var toolsEl = document.createElement('div');
        toolsEl.className = 'tool-area';
        QTB.buildToolReplay(toolsEl, data.tool_logs);
        rewriteImages(toolsEl, sessionId);

        // 顶部按钮
        if (data.scores_md) addReportButton('Score Report', data.scores_md);
        if (data.cost_md) addReportButton('Cost Report', data.cost_md);
        if (data.trace_md) addReportButton('Trace Report', data.trace_md);

        // 组装到 app
        ...
    });
}
```

---

## 7. HTML 模板

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QuantTutorBench</title>
    <link rel="stylesheet" href="/static/css/styles.css">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
    <script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
</head>
<body>
    <nav id="nav">
        <div class="nav-brand"><a href="#/results" class="nav-logo">QuantTutorBench</a></div>
        <div class="nav-links">
            <a href="#/results" class="nav-link">Results</a>
            <a href="#/tasks" class="nav-link">Tasks</a>
        </div>
    </nav>
    <main id="app"></main>
    <div id="modal-overlay" class="modal-overlay" style="display:none">
        <div class="modal-content">
            <div class="modal-header">
                <span class="modal-title"></span>
                <button class="modal-close" onclick="document.getElementById('modal-overlay').style.display='none'">&times;</button>
            </div>
            <div class="modal-body"></div>
        </div>
    </div>
    <script src="/static/js/render.js"></script>
    <script src="/static/js/chat.js"></script>
    <script src="/static/js/tools.js"></script>
    <script src="/static/js/app.js"></script>
</body>
</html>
```

---

## 8. 不要做的事

1. **不要用 FastAPI router**（`app.include_router`）——当前 server 是 Starlette，用 `Route()` 列表
2. **不要复制 legacy 的 SSE 系统**
3. **不要复制 legacy 的 run/eval 执行逻辑**
4. **不要实现 Evaluate / Re-evaluate 按钮**（首轮不做）
5. **不要实现 Dashboard 页面**（首轮不做）
6. **不要在 app.js 中做 sub-page DOM 缓存**
7. **不要直接读 results/run-single/**（那是 legacy 路径）
8. **不要在前端做 content-based 错误检测**（server 的 `success` 字段已正确）
9. **不要做 live monitoring**

---

## 9. 验收标准

### 功能验收

- [x] 访问 `http://localhost:8000/` 进入首页，默认展示 Results 列表
- [x] Results 列表正确显示所有 `results/server/` 下的 archived 结果
- [x] filter bar 按 category / eval_status 过滤生效
- [x] 搜索框按 task_id 模糊匹配生效
- [x] 点击结果进入详情页，三栏布局正确
- [x] 中栏对话正确回放（含 content_blocks inline rendering）
- [x] 有 client_trace 时 thinking block 可折叠展开
- [x] 无 client_trace 时降级为纯文本回放（不报错）
- [x] 右栏工具列表正确显示（含 success/fail 状态）
- [x] `send_message` 不再混入右栏 Tools，而是在 Conversation 中单独作为协议通信事件展示
- [x] 工具卡片点击弹窗显示完整 args/result（有滚动条）
- [x] 图片在 tool result 和 inline 中正确显示（非 404）
- [x] 图片在弹窗中也正确显示
- [x] Score Report 弹窗正确渲染 markdown（含 `<details>` 折叠）
- [x] Cost / Trace Report 弹窗正确显示
- [x] Workspace Explorer 通过右上角按钮打开，文件在弹窗内预览而不是跳转新 HTML 页面
- [x] Tasks 页面按 category 分组展示
- [x] 导航栏 Results / Tasks 切换正常

### 视觉验收

- [x] 暖色主题一致（amber/brown/beige）
- [x] 消息气泡样式一致（tutor 左、student 右）
- [x] 工具卡片样式一致
- [x] 导航栏样式一致
- [x] 左侧 Info 与右侧 Tools 均为侧边可展开/收起面板，收起条位置贴合侧栏
- [x] 顶部详情状态栏固定，Info / Conversation / Tools 三个板块各自滚动

### 技术验收

- [ ] 浏览器人工 smoke：无 JavaScript 控制台错误
- [x] JSON 中的 NaN/Infinity 不导致解析失败
- [x] 路径遍历攻击被阻止（`resolve()` + `relative_to()` 后不能逃出 `agent_files/` 根目录）
- [x] 弹窗长内容有滚动条
- [x] Workspace preview 对 markdown/json/csv/code/text/image/binary 做类型化展示或安全降级

### 已发现的后续补强项

- [x] `ResultIndexer._load_client_trace()` 需要兼容 server `session_id={uuid}_{task_prefix}` 与 client trace 目录 `{uuid}` 的映射，避免新存储 id 口径下误判 `has_client_trace=false`。
- [x] `send_message_events` 需要继续补充附件展示：当前 server conversation 已记录 attachment metadata，但 UI 的协议卡片还没有显式展示附件列表/预览入口。
- [x] `bench/server/web/README.md` 需要同步 Workspace routes 与 `send_message` 拆分后的 read model。

---

## 10. 代码锚点（Codex 参考）

| 需求 | 参考文件 | 关键函数（精确名） |
|------|---------|-------------------|
| server 路由结构 | `bench/server/api/http_app.py:654-689` | `create_app()` |
| ASGI 路由 | `bench/server/api/http_app.py:638-651` | `_ServerApp.__call__()` |
| 结果目录扫描 | `bench/web/api/results.py` | `_scan_results_dir()` |
| JSON sanitize | `bench/web/api/results.py` | `_sanitize_for_json()` |
| 任务列表 | `bench/web/api/tasks.py` | `list_tasks()` |
| 对话回放 | `bench/web/static/js/chat.js:452-487` | `buildConversationReplay()` |
| content_blocks 渲染 | `bench/web/static/js/chat.js:249-331` | `createContentBlocksEl()` |
| thinking block 读取 | `bench/web/static/js/chat.js:266` | `block.text \|\| ''` |
| 工具面板 | `bench/web/static/js/tools.js:225-287` | `buildToolReplay()` |
| 工具弹窗 | `bench/web/static/js/tools.js:46-136` | `showToolModal()` |
| 弹窗 glue | `bench/web/static/js/tools.js:48` | `window._qtbShowModal(title, html)` |
| 图片 rewrite (replay) | `bench/web/static/js/app.js:3707-3724` | `rewriteImages(container, pathStr)` |
| Markdown 渲染 | `bench/web/static/js/render.js:1-93` | `renderMarkdown()` |
| CSS 三栏布局 | `bench/web/static/css/styles.css:259-356,670-750` | `.page-run` / `.run-layout` / `.run-main` |
| CSS content_blocks | `bench/web/static/css/styles.css:1722-1862` | `.cb-*` 选择器族 |
| CSS 工具卡片 | `bench/web/static/css/styles.css:849-906` | `.tool-call` / `.tool-header` |
| CSS 消息气泡 | `bench/web/static/css/styles.css:703-807` | `.msg` / `.bubble` |
| CSS 弹窗 | `bench/web/static/css/styles.css:1390-1461` | `.modal-*` |
| server run_state | `bench/server/storage/result_writer.py:28-121` | `save_run_state()` — 含 `timestamp` 字段 |
| 新结果路径构建 | `bench/server/api/session_api.py:630-640` | `_save_results()` — `{task}/{persona}/{ts}_{sid[:8]}` |
| 新旧布局兼容扫描 | `bench/server/api/http_app.py:300-320` | `find_archived_result_dir()` |
| client trace | `bench/client/trace_writer.py:21-57` | `save_client_trace()` |
| eval 结果 | `bench/server/storage/eval_writer.py:30-148` | `run_evaluation()` |
| eval status 更新 | `bench/server/storage/result_writer.py:232-244` | `update_evaluation_status()` |
| session 评分 | `bench/server/api/session_api.py:617-685` | `SessionState._run_evaluation()` |

---

## 11. 分阶段执行计划（按对话拆分，避免上下文爆炸）

### 总原则

- 每一轮对话只做一个清晰边界的交付，不跨越后端聚合层和前端复杂渲染层
- 每一轮完成后都更新本文档中的完成状态，并在代码里留下可运行的中间产物
- 下一轮开始时，只需重新读取：
  - 本文档
  - 本轮相关文件
  - 上一轮新增/修改的代码
- 不要求在单轮对话中同时完成“UI API + 前端列表 + 详情页 + legacy 组件迁移”

### Phase 1：后端骨架与静态挂载

**状态：已完成。**

**目标**：
- 建立 `bench/server/web/` 目录
- 完成 `http_app.py` 中的 Starlette 接线
- `/` 能返回 `index.html`
- `/static/*` 能正确服务静态资源
- `/ui/tasks` / `/ui/results` / `/ui/results/{session_id}` / `/ui/results/{session_id}/files/{path:path}` 路由先返回最小可用响应或 stub

**本轮只关注**：
- `bench/server/api/http_app.py`
- `bench/server/web/ui_app.py`
- `bench/server/web/templates/index.html`

**不要在本轮做**：
- merged read model
- legacy JS/CSS 迁移
- 详情页渲染

### Phase 2：`ResultIndexer` 与 `/ui/*` 真正数据层

**状态：已完成。**

**目标**：
- 实现 `ui_indexer.py`
- `/ui/tasks` 返回真实任务/人格数据
- `/ui/results` 返回真实 summary 列表
- `/ui/results/{session_id}` 返回真实 merged detail
- `/ui/results/{session_id}/files/{path:path}` 安全可用

**本轮只关注**：
- `bench/server/web/ui_indexer.py`
- `bench/server/web/ui_app.py`

**验收点**：
- 可直接 `curl`/浏览器访问 UI API，拿到完整 JSON
- 无 client trace 的 session 能正常降级
- report 读取走 `_resolve_latest_eval_dir()` fallback

### Phase 3：前端 Shell 与 Results/Tasks 列表

**状态：已完成。**

**目标**：
- 建立最小 `app.js`
- 建立导航、Results 列表页、Tasks 页
- 完成 filter bar 和 session card 的基本渲染
- 页面可从 `#/results` 和 `#/tasks` 切换

**本轮只关注**：
- `bench/server/web/static/js/app.js`
- `bench/server/web/templates/index.html`
- `bench/server/web/static/css/styles.css` 的结构性裁剪

**不要在本轮做**：
- 详情页 conversation replay
- tools replay
- report modal 深度适配

### Phase 4：详情页与 legacy 回放组件迁移

**状态：已完成，并已额外完成 send_message 协议事件拆分、左右栏 legacy 风格折叠、Workspace Explorer。**

**目标**：
- 迁移 `render.js`、`chat.js`、`tools.js`
- 完成三栏详情页
- 实现 content_blocks inline replay
- 实现工具卡片与 modal
- 完成图片 rewrite（普通区域 + modal）

**本轮只关注**：
- `bench/server/web/static/js/chat.js`
- `bench/server/web/static/js/tools.js`
- `bench/server/web/static/js/render.js`
- `bench/server/web/static/js/app.js`
- `bench/server/web/static/css/styles.css`

**验收点**：
- 至少用一个有 `client_trace.json` 的 session 验证 thinking/tool replay
- 至少用一个无 `client_trace.json` 的 session 验证纯文本降级

### Phase 5：收尾、验证与文档回填

**状态：基本完成。剩余项见第 9 节“已发现的后续补强项”。**

**目标**：
- 做样式清理与小问题修正
- 补充报错处理、空态、按钮显隐
- 对照第 9 节验收标准逐项自测
- 回填本文档中实际偏差与最终实现说明

**本轮只关注**：
- bug fix
- polish
- 验证

**不要在本轮扩 scope**：
- 不新增 Dashboard
- 不新增 Evaluate/Re-evaluate
- 不新增 live monitoring

### Phase 6：Run 界面（先于 Eval）

**状态：第一版 REST session harness 已实现，并已完成 Human / Agent 模式拆分。**

**目标**：
- 在新隔离前端中新增 Run 页面，支持从 task/persona 创建并启动新 client-server session。
- Run 页面优先复刻 legacy 的任务选择、人格选择、运行状态、对话/工具/信息布局，但不复用 legacy 路径或 legacy run/eval 逻辑。
- Run 页面需要以新架构为准：`register_session` / `start_session` / `send_message` / domain tools 都走 `/session/*` 或 MCP-compatible server API，不直接读写 legacy `results/run-single/`。
- 运行过程产生的结果仍落到 `results/server/` 与 `results/client/`，完成后可跳转到现有 Results detail 页面继续查看 workspace、reports、trace。

**当前第一版边界**：
- 进入 Run 时必须先选择 `Human Test` 或 `Agent Test`。
- `Human Test` 是浏览器侧 REST session harness：注册、启动、显示 client-visible background/student opening、发送 tutor 消息/附件、刷新状态/tools、取消、完成后跳 Results。
- `Agent Test` 对齐 `bench/client/runner.py` 的真实 MCP 流程，但当前只展示执行边界和配置入口，`Start Agent Run` 保持禁用。
- 第一版不启动 `bench/client/runner.py`，也不管理本地 agent 子进程；autonomous client launch 需要单独设计后端进程管理和安全边界。
- Run 执行界面只显示公开 task label（如 `D01` / `X09`），不暴露完整 task id、category、difficulty、description；Info 栏只显示 client-visible runtime context。

**已完成**：
- 新增 `#/run` SPA route 和导航入口。
- 复用 `/ui/tasks` 读取 task/persona catalog。
- 接入 `/session/register`、`/session/{sid}/start`、`/session/{sid}`、`/session/{sid}/tools`、`/session/{sid}/send`、`DELETE /session/{sid}`。
- 前端维护本轮运行的 conversation preview，完成后提供跳转 Results detail。
- Run 入口拆为 Agent / Human；Human 模式保留可执行 REST harness，Agent 模式明确等待后端 job launcher。
- Run 布局改为左 Info / 中 Conversation / 右 Tools，Conversation 占主宽度，实时工具可见性与 live activity 放在右侧。
- 增加 action 状态机（registering / starting / refreshing / sending / cancelling），执行中锁定 start/send/refresh/cancel/reset/mode 切换等按钮，避免重复点击。

**本轮只关注**：
- Human Test 的 REST session lifecycle 闭环。
- Agent Test 的真实流程说明与禁用态边界，不在前端硬启 client runner。
- 最小可用的运行状态展示、按钮锁定与完成后跳转。
- 确保 Run 视图不泄漏 hidden task metadata。

**暂不做**：
- Evaluate / Re-evaluate UI。
- Dashboard。
- 完整 live monitoring/SSE。第一版 Run 页面可以用轮询或手动刷新状态，避免先把实时系统做复杂。
- Web 启动自动化 agent job。需要新增后端 job manager、subprocess 生命周期、日志/工具事件轮询或 SSE、取消与结果映射后再开启。

### Phase 7：Evaluation / Re-evaluation UI

**状态：Phase 6 完成后再做。**

**目标**：
- 在 Results detail 或独立 Eval 面板中接入 `POST /session/{sid}/evaluate?force=true&eval_mode=...&tutor_dims=...`。
- 支持 eval history、latest report 切换、eval_mode/tutor_dims 参数展示与触发。
- 对齐新的 rubric/eval pipeline，不再按旧 eval UI 假设实现。

### 建议的对话节奏

1. 对话 1：Phase 1 已完成
2. 对话 2：Phase 2 已完成
3. 对话 3：Phase 3 已完成
4. 对话 4：Phase 4 已完成
5. 对话 5：Phase 5 基本完成
6. 对话 6：先修复第 9 节中影响 Run/Results 串联的补强项，然后实现 Phase 6 Run 页面最小闭环
7. 对话 7：Run 页面 polish 与人工视觉审核
8. 对话 8：进入 Phase 7 Evaluation / Re-evaluation UI

如果某一轮实现量超出预期，优先在该 phase 内再拆一轮，而不是把下一 phase 提前混进来。
