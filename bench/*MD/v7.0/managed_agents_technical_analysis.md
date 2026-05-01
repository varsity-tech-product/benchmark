# Claude Managed Agents Technical Analysis

> Created: 2026-04-30
> Sources:
> - Anthropic Engineering: [Scaling Managed Agents: Decoupling the brain from the hands](https://www.anthropic.com/engineering/managed-agents)
> - Claude API Docs: [Claude Managed Agents overview](https://platform.claude.com/docs/en/managed-agents/overview)
> - Related docs: [Quickstart](https://platform.claude.com/docs/en/managed-agents/quickstart), [Sessions](https://platform.claude.com/docs/en/managed-agents/sessions), [Events and streaming](https://platform.claude.com/docs/en/managed-agents/events-and-streaming), [Tools](https://platform.claude.com/docs/en/managed-agents/tools), [Environments](https://platform.claude.com/docs/en/managed-agents/environments), [MCP connector](https://platform.claude.com/docs/en/managed-agents/mcp-connector), [Vaults](https://platform.claude.com/docs/en/managed-agents/vaults), [Permission policies](https://platform.claude.com/docs/en/managed-agents/permission-policies), [Files](https://platform.claude.com/docs/en/managed-agents/files), [Observability](https://platform.claude.com/docs/en/managed-agents/observability)

---

## 1. Executive Summary

Claude Managed Agents is best understood as a hosted agent runtime, not just Claude with a larger tool list. It provides the infrastructure required to run Claude as an autonomous, long-horizon agent: an agent harness, durable session storage, cloud sandboxes, built-in tools, MCP integration, credential handling, event streaming, and observability.

The most important architectural idea is the separation of the agent "brain" from the agent "hands." The brain is Claude plus the harness that decides what to do next. The hands are execution environments and tools, such as cloud containers, bash, file operations, web tools, MCP servers, and custom tools. A third piece, the session, is the durable append-only event log that records what happened.

This design makes the system more reliable, more secure, and easier to evolve as model capabilities change. Anthropic's engineering post frames Managed Agents as a stable interface layer around components that may change underneath: session, harness, and sandbox.

---

## 2. What Problem Managed Agents Solves

### 2.1 It removes the need to build a full agent harness

Without Managed Agents, an application developer typically has to build and operate:

- A loop that repeatedly calls the model.
- Tool-call parsing and dispatch.
- Tool-result insertion into the next model call.
- Conversation history management.
- Context compaction and trimming.
- Sandboxed code execution.
- File management.
- Retry and crash recovery.
- Credential isolation.
- Event streaming to the frontend.
- Observability and cost tracking.

Managed Agents moves most of this into Anthropic's managed infrastructure. Developers create reusable agent definitions, create environments, start sessions, send events, and stream back agent progress.

### 2.2 It targets long-running and asynchronous work

The product documentation positions Managed Agents as complementary to the Messages API:

| Interface | Best suited for |
| --- | --- |
| Messages API | Direct model prompting, custom agent loops, fine-grained control |
| Claude Managed Agents | Long-running tasks, asynchronous execution, managed tools and infrastructure |

This means Managed Agents is more appropriate for tasks that run for minutes or hours and require multiple tool calls, filesystem state, code execution, web access, or external service integration.

### 2.3 It avoids fragile assumptions in hand-written harnesses

The engineering post argues that agent harnesses often encode assumptions about what the model cannot do. Those assumptions can go stale as models improve. For example, Anthropic previously added context resets to address "context anxiety" in one model, but later found that the behavior disappeared in a stronger model, making that harness feature unnecessary.

Managed Agents is therefore designed as a meta-harness: it standardizes the interfaces around the agent while leaving room for Anthropic to change the internal harness implementation over time.

---

## 3. Core Architecture

Managed Agents is built around four product-level concepts:

| Concept | Description |
| --- | --- |
| Agent | A reusable, versioned configuration containing the model, system prompt, tools, MCP servers, skills, and optionally callable agents. |
| Environment | A cloud container template with packages, runtime configuration, network access rules, and mounted resources. |
| Session | A running agent instance inside an environment, performing a specific task and preserving state across interactions. |
| Events | Messages exchanged between the application and the agent, including user messages, agent messages, tool calls, tool results, status updates, and errors. |

At the engineering level, the architecture can be described as:

```text
Application
  |
  | user events / stream events
  v
Managed Session Log  <---->  Stateless Harness / Brain  <---->  Tools / Sandboxes / MCP / Custom Tools
      durable                    replaceable                    disposable or externally hosted
```

The session log is outside the harness. The sandbox is outside the harness. Credentials are outside the sandbox. This separation is the main technical move.

---

## 4. Brain, Hands, and Session

### 4.1 Brain

The brain is Claude plus the agent harness. It reads session events, decides what Claude should see in its current context window, calls the model, and routes tool calls to the appropriate tool infrastructure.

Because the harness is stateless relative to the durable session log, it can crash and be restarted. A new harness can wake up with a session ID, read the event history, and continue from the last event.

### 4.2 Hands

Hands are the environments and tools that perform actions:

- Cloud containers.
- Bash execution.
- File reads, writes, edits, glob, and grep.
- Web search and web fetch.
- MCP tools.
- Custom tools executed by the developer's own application.

Anthropic describes each hand as fitting a generic tool interface: a name and input go in, and a result comes back. The harness does not need to know whether the underlying hand is a container, a remote MCP server, or a custom service.

### 4.3 Session

The session is an append-only log of everything that happened. It is not the same as Claude's context window.

This distinction matters. A context window is limited, mutable, and subject to compaction or trimming. A session log is durable and can be queried. The harness can select slices of prior events, transform them, summarize them, or arrange them to improve prompt-cache efficiency, but the original history remains recoverable until the session is deleted.

---

## 5. Reliability Model

### 5.1 The old coupled design created "pet" containers

Anthropic describes an earlier design where the session, harness, and sandbox lived inside the same container. That made direct file edits simple, but it also made the container a stateful "pet":

- If the container failed, the session could be lost.
- If the container became unresponsive, engineers had to debug that specific instance.
- Failures in the event stream, harness, or container were hard to distinguish.
- Debugging was harder because user data lived in the same environment.

### 5.2 The new design treats components as replaceable

In the decoupled design:

- If a sandbox dies, the harness receives a tool-call failure and Claude can decide whether to retry.
- If a harness dies, another harness can resume from the durable event log.
- If a session has not yet needed a container, no container needs to be provisioned.
- Each failure has a clearer boundary: session storage, harness, tool execution, MCP, or external service.

This is the reliability benefit of decoupling the brain from the hands.

### 5.3 Latency improves because containers are provisioned lazily

In the coupled model, every session had to wait for a container before the model could begin work. In the decoupled model, inference can start as soon as the orchestration layer reads pending session events. A container is provisioned only when a tool call actually needs it.

Anthropic reports that this reduced time-to-first-token by roughly 60% at p50 and over 90% at p95.

---

## 6. Security Model

### 6.1 Credentials should not be reachable from the sandbox

The engineering post identifies a major risk in coupled agent systems: if Claude-generated code runs in the same container as credentials, a prompt injection can try to convince Claude to read its own environment. Once tokens are exposed, the attacker may be able to create new sessions or call external services directly.

Managed Agents addresses this structurally by keeping tokens outside the sandbox.

### 6.2 Git credentials can be bound to resources

For Git operations, Anthropic describes using repository access tokens to clone repositories during sandbox initialization and wiring them into the local git remote. This allows `git push` and `git pull` to work without exposing the token directly to Claude or to code running in the sandbox.

### 6.3 MCP credentials are stored in vaults

For MCP tools, credentials are stored in vaults. The agent definition declares MCP servers by name and URL, but does not contain secrets. At session creation time, the developer passes `vault_ids`. The runtime matches active credentials to MCP server URLs and injects the relevant token through a proxy.

This split is important:

- Agent definitions remain reusable and secret-free.
- Sessions can authenticate on behalf of different end users.
- OAuth tokens can be refreshed by Anthropic if refresh information is provided.
- Secret fields are write-only and are not returned in API responses.

### 6.4 Permission policies control server-executed tools

Managed Agents supports permission policies for built-in agent tools and MCP tools:

| Policy | Behavior |
| --- | --- |
| `always_allow` | The tool runs automatically. |
| `always_ask` | The session pauses and waits for a `user.tool_confirmation` event. |

This provides a human-in-the-loop control point for sensitive actions, especially shell commands or external service mutations.

Custom tools are different: they are executed by the developer's application, so the application itself decides whether to run the requested operation before returning a `user.custom_tool_result`.

---

## 7. API and Runtime Flow

### 7.1 Create an agent

An agent defines the reusable behavior and capabilities:

- Model.
- System prompt.
- Built-in tools.
- MCP toolsets.
- Custom tools.
- Skills.
- Callable agents for multi-agent orchestration.
- Metadata.

Agents are versioned. Updating an agent creates a new version, which lets developers pin a session to a specific version or use the latest version.

### 7.2 Create an environment

An environment defines the cloud container configuration:

- Pre-installed packages.
- Network policy.
- Package manager access.
- MCP server network access.
- Mounted resources.

Multiple sessions can reference the same environment, but each session gets its own isolated container instance. Sessions do not share filesystem state.

### 7.3 Start a session

A session binds an agent to an environment. Creating the session provisions the environment and agent, but work begins only when the application sends a user event.

Sessions can be in several states:

| Status | Meaning |
| --- | --- |
| `idle` | Waiting for user input or required tool confirmations. |
| `running` | Actively executing. |
| `rescheduling` | Recovering from a transient error and retrying automatically. |
| `terminated` | Ended because of an unrecoverable error. |

### 7.4 Send events and stream responses

The interaction model is event-based:

- The application sends `user.message` to start or continue work.
- Claude emits `agent.message`, `agent.tool_use`, `agent.custom_tool_use`, `agent.mcp_tool_use`, and related events.
- The session emits status events such as `session.status_idle`.
- The application can send `user.interrupt` to redirect a running session.
- The application can approve or deny tool calls with `user.tool_confirmation`.
- The application can return custom tool outputs with `user.custom_tool_result`.

Streaming uses server-sent events, so the application can show real-time progress while the agent works.

---

## 8. Tools and Execution

### 8.1 Built-in tools

The default `agent_toolset_20260401` includes:

| Tool | Name | Purpose |
| --- | --- | --- |
| Bash | `bash` | Execute shell commands in a shell session. |
| Read | `read` | Read files from the local filesystem. |
| Write | `write` | Write files to the local filesystem. |
| Edit | `edit` | Perform string replacement in files. |
| Glob | `glob` | Match files using glob patterns. |
| Grep | `grep` | Search text using regex patterns. |
| Web fetch | `web_fetch` | Fetch content from URLs. |
| Web search | `web_search` | Search the web. |

The full toolset can be enabled, individual tools can be disabled, or the default can be set to disabled with only selected tools enabled.

### 8.2 Custom tools

Custom tools work similarly to user-defined client tools in the Messages API. Claude emits a structured tool request, but the developer's application executes the operation and sends the result back.

This pattern is useful when:

- The operation must run in the developer's own infrastructure.
- The operation requires application-specific authorization.
- The result should be filtered or normalized before being returned to Claude.

Good custom tool design should use detailed descriptions, meaningful namespacing, consolidated operations, and high-signal responses.

### 8.3 MCP tools

Managed Agents can connect to remote MCP servers that expose streamable HTTP transport. MCP configuration is split into:

1. Agent-level declaration of server name and URL.
2. Session-level authentication via vault IDs.

MCP toolsets default to `always_ask`, which prevents newly added MCP tools from executing automatically without approval unless the developer explicitly relaxes the policy.

---

## 9. Files and Container State

Files can be provided to an agent by uploading them through the Files API and mounting them into the session container as resources.

Important properties:

- Mounted files are read-only copies.
- The original uploaded file is not modified.
- The agent can write modified outputs to new paths inside the container.
- Paths should be absolute.
- Parent directories are created automatically.
- A session can mount up to 100 files.

When a session goes idle, the container is checkpointed. The checkpoint preserves filesystem state, installed packages, and files created by the agent. Session history persists until deletion, but idle container checkpoints are preserved for 30 days after last activity.

---

## 10. Environment and Networking

Cloud environments support package installation through package managers such as `apt`, `cargo`, `gem`, `go`, `npm`, and `pip`. Packages are installed before the agent starts and are cached across sessions that share the environment.

Networking can be configured as:

| Mode | Meaning |
| --- | --- |
| `unrestricted` | Full outbound network access except for a general safety blocklist. |
| `limited` | Restricts access to explicit allowed hosts, with separate flags for package managers and MCP servers. |

For production deployments, the documentation recommends `limited` networking with explicit `allowed_hosts`, following least privilege.

---

## 11. Observability and Debugging

Managed Agents provides both Console-based and API-based observability.

The Console includes:

- Session list with status, creation time, and model.
- Timeline view of session events.
- Token usage.
- Tool execution details.

The API can retrieve raw events for programmatic debugging. Useful signals include:

- `session.error` for runtime failures.
- Tool use and tool result events for action tracing.
- `span.model_request_end` for token accounting.
- Session-level cumulative usage for cost monitoring.

This is a direct benefit of the event-log architecture: the same event stream supports product UX, debugging, auditability, and cost tracking.

---

## 12. Multi-Agent Orchestration

Managed Agents also has a research-preview multi-agent mode. One coordinator agent can call other configured agents. Each called agent gets its own isolated session thread and conversation history, while all agents share the same container and filesystem.

This is useful for decomposable tasks such as:

- Code review by a read-only reviewer agent.
- Test generation by a test-writing agent.
- Research by a web-search-focused agent.

The coordinator can delegate to callable agents, but only one level of delegation is supported. Subagents cannot recursively call their own subagents.

---

## 13. Engineering Implications

The design resembles a small operating-system abstraction for agents:

| Agent runtime concept | Operating-system analogy |
| --- | --- |
| Session log | Durable process/event history |
| Harness | Scheduler and runtime loop |
| Sandbox | Process or execution environment |
| Tool call | System call or RPC |
| Vault | External secret manager |
| MCP/custom tools | Device drivers or external services |

The value is not only convenience. The main value is that the interfaces can remain stable while the implementation underneath changes. Anthropic can update context-management strategies, model invocation patterns, sandbox provisioning, or tool routing without requiring developers to rewrite their applications.

---

## 14. Fit for Benchmark and Agent Evaluation Systems

For benchmark systems, Managed Agents has several implications:

1. It provides a standard way to run long-horizon agents without implementing a full harness locally.
2. It produces a durable event trail that can be used for evaluation, replay, auditing, and failure analysis.
3. It separates task execution from credential handling, reducing the risk of benchmark sandboxes leaking secrets.
4. It supports custom tools, so benchmark-specific evaluators or domain tools can remain under benchmark-server control.
5. It supports mounted files and isolated session containers, which maps well to task-specific workspaces.
6. It may reduce comparability with fully self-hosted agents if the managed harness performs hidden context management or optimizations. Benchmark design should document whether the evaluated system is Claude Managed Agents specifically or a generic agent using Claude through the Messages API.

For a benchmark like QuantTutorBench, the closest architectural parallel is the server/client decoupling pattern: the benchmark server owns task state, tools, environment facts, and scoring, while the agent runtime owns reasoning and tool selection. Managed Agents moves much of that runtime into Anthropic's platform.

---

## 15. Limitations and Practical Cautions

Managed Agents is currently beta and requires the `managed-agents-2026-04-01` beta header. Behaviors may change between releases.

Important constraints and cautions:

- It is less suitable when the developer needs complete control over every model call.
- It is less transparent than a fully custom loop because the hosted harness may perform internal context management.
- Environments are not versioned, so teams that frequently update environments should track environment changes themselves.
- Sessions preserve history until deletion, but idle container checkpoints are only preserved for 30 days after last activity.
- MCP credentials are workspace-scoped; anyone with suitable API key access can use vaults to authorize sessions.
- Production deployments should use limited networking and explicit domain allowlists.
- Permission policies should be stricter for risky tools such as bash or external mutation tools.

---

## 16. Bottom Line

Claude Managed Agents is a hosted agent operating layer. Its key contribution is the separation of durable session state, stateless harness execution, disposable or externalized tools, and isolated credential handling.

This architecture improves reliability because failures are localized and recoverable. It improves security because secrets do not need to live inside the execution sandbox. It improves latency because containers can be provisioned only when needed. It improves product integration because applications communicate through events rather than managing a full model/tool loop.

The tradeoff is reduced control and lower transparency compared with a custom Messages API agent loop. For long-running asynchronous tasks where infrastructure, recovery, and secure tool execution matter more than per-call control, Managed Agents is a strong fit.
