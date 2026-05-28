# Agent Provider Abstraction Proposal

## Background

Edict already provides a multi-agent and multi-subagent workflow console around the Three Departments and Six Ministries model. It coordinates task intake, role-based planning, review, dispatch, execution, logging, audit trails, and archival views through the existing OpenClaw-based workflow.

This gives Edict a strong control plane: users can observe where a task is, which role owns the next step, how execution is progressing, and how the final memorial is produced.

## Problem

As Edict grows, the orchestration layer and the execution layer may need to evolve at different speeds.

If task execution is tightly coupled to one specific agent runtime, it becomes harder to experiment with alternative execution backends without touching the core workflow. For example, a future integration may want to call a local shell script, a CI job, a Codex session, a Claude Code session, or another specialized runtime while keeping the same dashboard, status model, review flow, and audit trail.

The goal is not to replace the current OpenClaw workflow. The goal is to make the execution boundary explicit so that future providers can be explored without weakening the existing system.

## Proposal

Introduce an `AgentProvider` abstraction layer between Edict's orchestration workflow and the concrete execution backend.

At a high level, Edict would keep owning:

- task lifecycle and state transitions
- Three Departments and Six Ministries role semantics
- review and dispatch policy
- dashboard visibility
- audit logs and memorial output

The provider would own:

- how a task is sent to a concrete execution runtime
- how execution status is queried
- how cancellation is requested
- how provider-specific logs or outputs are normalized back into Edict

Example interface:

```ts
interface AgentProvider {
  name: string
  runTask(input: TaskInput): Promise<TaskResult>
  getStatus(taskId: string): Promise<TaskStatus>
  cancelTask(taskId: string): Promise<void>
}
```

This interface is intentionally small. It should describe the minimum contract needed by the orchestration layer rather than exposing provider-specific details.

## Example Providers

- `OpenClawProvider`: wraps the existing OpenClaw-based runtime path.
- `ShellProvider`: runs a configured local command or script for simple local workflow integration.
- `CodexProvider`: explores Codex-backed task execution for coding or repository workflows.
- `ClaudeCodeProvider`: explores Claude Code-backed task execution for local development workflows.

These providers do not need to be implemented at the same time. The abstraction can start as a design boundary and grow only when real integration needs appear.

## ClaudeCodeProvider: Guarded Code Modification Workflow

`ClaudeCodeProvider` could support code-modification tasks, but it should do so through a guarded workflow rather than direct uncontrolled repository edits.

A possible flow:

```text
Edict task
  -> ClaudeCodeProvider
  -> isolated workspace or contribution branch
  -> patch, test output, failure notes, and change summary
  -> Edict review gate
  -> optional commit or push after approval
```

In this model, Edict remains the control plane. Claude Code can propose and produce code changes, but Edict decides how those changes are reviewed, logged, approved, rejected, or archived.

Recommended safety boundaries:

- run code changes in an isolated workspace, worktree, or branch
- return a patch or commit candidate instead of editing protected branches directly
- include changed files, test output, and known failure notes in the task result
- require human approval or a review-stage policy before commit, push, or merge
- prevent provider tasks from deleting, moving, or overwriting unrelated files by default

This allows Claude Code to be useful for real implementation work while preserving Edict's existing review and audit model.

## First Step: ShellProvider

A minimal first implementation could be `ShellProvider`.

The purpose of `ShellProvider` would be to call a user-configured local script while Edict keeps its current task lifecycle and visibility model. This would make it possible to test the provider boundary without depending on a new vendor runtime.

Example configuration:

```yaml
provider: shell
workspace: ./examples/local-workflow
command: bash scripts/run-task.sh
```

The script could receive task input through stdin, environment variables, or a temporary JSON file. Its output could be captured and normalized into Edict's existing task result and log format.

## Benefits

- Keeps the existing OpenClaw workflow unchanged.
- Reduces execution-layer coupling.
- Enables local workflow integration.
- Makes future Codex, Claude Code, shell, and CI integration easier.
- Allows downstream personal workflow consoles to build on top of Edict.

## Non-goals

- Does not replace OpenClaw.
- Does not rewrite the UI.
- Does not change the existing Three Departments and Six Ministries workflow.
- Does not introduce vendor-specific dependencies.
- Does not require implementing every provider at once.
- Does not allow providers to bypass review gates before committing, pushing, or merging code changes.

## Possible Downstream Use Cases

Possible downstream use cases include:

- personal AI workflow consoles
- local script workflows
- Codex or Claude Code collaboration workflows
- CI or automation workflows

For example, a downstream project such as MemoFlow AI could use Edict as a workflow console while connecting its own local execution scripts through a provider boundary. This should remain a downstream integration example rather than the main direction of Edict itself.

## Migration Strategy

P0: Keep current behavior unchanged.

P1: Introduce an `AgentProvider` interface.

P2: Add a `ShellProvider` prototype.

P3: Wrap the existing runtime as `OpenClawProvider`.

P4: Add provider configuration examples.

P5: Explore workflow templates.

This order keeps the current OpenClaw path stable while allowing provider experiments to happen behind a narrow contract.

## Risks

### Shell execution security

Local command execution can be dangerous if task input is passed directly into shell commands. A shell provider should avoid string interpolation, document trust boundaries, and prefer explicit command arguments or structured input files.

### Permission boundaries

Different providers may have different filesystem, network, credential, and process permissions. Edict should make provider permissions visible and configurable instead of assuming all providers are equally trusted.

### Cross-platform path compatibility

Shell commands and workspace paths differ across macOS, Linux, Windows, and WSL. Provider configuration should avoid platform-specific assumptions where possible, and examples should clearly state their target environment.

### Task status synchronization

Providers may report status differently. Edict should normalize provider states into its own task lifecycle instead of letting provider-specific states leak into the core workflow.

### Logging format consistency

Provider logs should be normalized so that the dashboard, audit views, and memorial archive can remain consistent across execution backends.

### Provider failure handling

Provider failures should be explicit and recoverable. Edict may need clear rules for retries, cancellation, timeout handling, partial output capture, and final error reporting.

### Code modification boundaries

Providers that can edit code, such as a future `ClaudeCodeProvider` or `CodexProvider`, should have strict workspace and branch boundaries. Edict should record the intended file scope, expose the resulting diff, and require an approval step before remote write operations.

## 简体中文说明

这份文档提出一个 `AgentProvider` 抽象层，用于讨论 Edict 未来如何在保持现有 OpenClaw 工作流不变的前提下，支持更多执行后端。

核心思路是：Edict 继续负责三省六部的任务流转、角色分工、审议、派发、日志、审计和归档；具体执行后端则通过 provider 接口接入。这样可以减少编排层和执行层之间的耦合。

可能的 provider 包括：

- `OpenClawProvider`：封装当前 OpenClaw 执行路径。
- `ShellProvider`：调用用户配置的本地脚本或命令，作为最小原型。
- `CodexProvider`：用于探索 Codex 驱动的代码或仓库任务。
- `ClaudeCodeProvider`：用于探索 Claude Code 驱动的本地开发任务。

其中，`ClaudeCodeProvider` 或 `CodexProvider` 可以支持真实代码修改，但不应绕过 Edict 的审核门禁。推荐方式是在隔离 workspace、worktree 或贡献分支中执行，返回 patch、测试输出、失败说明和变更摘要，再由 Edict 或人工审核决定是否 commit、push 或 merge。

这份提案不是要替换 OpenClaw，也不是要重写 UI 或立即实现所有 provider。它只是先把执行层边界抽象出来，方便后续在不破坏当前工作流的基础上，逐步探索 shell、Codex、Claude Code、CI 或下游个人工作流系统等集成方向。
