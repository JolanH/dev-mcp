---
name: start-task
description: Create an isolated dev task with the dev-helper MCP and implement it end-to-end via bmad-quick-dev, self-reporting status (running / blocked) as it goes. Use when the user says "start a task", "start-task", "kick off a task", or wants to create and immediately implement a scoped piece of work.
---

# Start Task

**Goal:** Turn a user request into a tracked, isolated dev task and implement it, keeping the task's status honest the whole way through.

This skill chains two things:

1. The **dev-helper MCP** (`mcp__dev-helper__create_task` / `mcp__dev-helper__update_task`) for task tracking + isolated worktrees.
2. The **`bmad-quick-dev`** skill for the actual implementation.

## Status model (non-negotiable)

The task's status MUST always reflect reality. `update_task` accepts exactly four states:

- `running` — actively being worked on ("in progress").
- `blocked` — **awaiting human input**. Set this whenever you are about to ask the user to confirm, decide, or clarify *anything*.
- `review` — implementation finished, awaiting review.
- `done` — terminal. A `done` task cannot be reactivated.

Legal transitions: any active state → any of the four. `done` is one-way.

## Workflow

### Step 1 — Gather the task name

`create_task` only strictly requires `task_name`. Ask the user for a short kebab-case task name if they did not give one. `description` and `repos` are optional:

- If `repos` is omitted, it defaults to the git repo containing the server's current directory.
- If `base_ref` is omitted, it defaults to the current branch's base.
- If you write a `description`, derive it from the user's request (a one-paragraph statement of intent is enough).

Do **not** invent repos or base refs — let the defaults apply unless the user named specific ones. If the defaults can't be derived the tool returns `NoDefaultRepo` / `NoDefaultBaseRef`; relay that and ask the user.

### Step 2 — Create the task

Call `mcp__dev-helper__create_task` with `task_name` (and `description` / `repos` / `base_ref` only if you have them).

- On success the envelope is `{ok: true, data: {task_id, ...}}`. **Capture `data.task_id`** — every later `update_task` call needs it.
- On `{ok: false}`, surface `error` to the user and stop; do not proceed to implementation.

### Step 3 — Mark it running

Immediately call `mcp__dev-helper__update_task` with the captured `task_id` and `status: "running"`. This records that work has started ("in progress") before any code is touched.

### Step 4 — Implement via bmad-quick-dev

Invoke the **`bmad-quick-dev`** skill to implement the task, passing the user's intent as the input. Work inside the task's worktree.

**While implementing, the blocked rule is in force at all times:**

> **Before you ask the user to confirm, decide, approve, or clarify ANYTHING** — including `bmad-quick-dev`'s own checkpoints and "wait for human input" halts — first call `mcp__dev-helper__update_task` with `status: "blocked"`, *then* ask your question. Do not ask the human while the task still reads `running`.

After the user responds and you resume work, call `update_task` with `status: "running"` again before continuing.

### Step 5 — Finish

When implementation is complete:

- Set `status: "review"` if the work is ready for the user to review (the typical end state).
- Only set `status: "done"` if the user explicitly confirms the task is finished and closed — remember `done` is terminal and releases the slug.

## Quick reference

| Moment | Call |
|---|---|
| Work begins | `update_task(task_id, status="running")` |
| About to ask the human anything | `update_task(task_id, status="blocked")` → then ask |
| Human answered, resuming | `update_task(task_id, status="running")` |
| Implementation done, awaiting review | `update_task(task_id, status="review")` |
| User confirms closure | `update_task(task_id, status="done")` |
