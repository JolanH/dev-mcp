# PRD Quality Review — dev-helper-mcp

## Overall verdict

This is a strong internal-tool PRD: it knows exactly what it is (a single-operator MCP control plane + read-only dashboard for git worktrees), states its decisions as decisions, and keeps capability separated from implementation via a disciplined addendum. The FRs are unusually testable, scope omissions are explicit and reasoned, and the differentiation thesis is concrete rather than aspirational. The main risks are minor: a couple of NFR targets lean on hedged adjectives ("a few seconds", "snappy") rather than committed bounds, and one assumption (A2) is double-tagged across two FRs without distinguishing what each contributes. Nothing here blocks the downstream UX/architecture/epics chain.

## Decision-readiness — strong

Decisions are surfaced as decisions, not smuggled in as "considerations." The Non-Goals section (§5) states each cut with a decision-log ID (`D3`, `D7`, `D8`, `D4`, `A3`, `R2`), and the Vision (§1) names the actual market bet plainly: "Almost nothing exposes worktree **and** task management as MCP tools an agent self-services, paired with a live monitoring view." That is a falsifiable positioning claim, not neutral smoothing.

Trade-offs name what was given up. The addendum §7 "Rejected / deferred alternatives" is where this earns its grade — container isolation rejected because "heavier (needs Docker)", TUI rejected despite competitor precedent because "user explicitly wants a web dashboard", tool-launches-agents rejected partly because "it would force long-running process supervision that fights MCP's synchronous tool model (R3)." Each rejection carries its cost.

The `[NOTE FOR PM]` callouts land at genuine tension, not safe checkpoints. The one at §6.2 — "worktree bootstrap is the most emotionally load-bearing deferral — fresh worktrees with no deps are a real friction point" — flags a real risk to the product's usefulness, exactly the kind of honesty the dimension wants. Open Questions (§8) are actually open: Q2–Q5 each carry an owner and a "Revisit: post-v1", and Q1 is shown resolved with a date and a strikethrough rather than quietly deleted.

## Substance over theater — strong

Little furniture here. The single UJ (UJ-1) is deliberately downscaled to scope — "*Downscaled per internal single-operator scope. One representative journey.*" — rather than padded to look thorough; this is the opposite of persona theater. There is exactly one persona (Dev), and it drives concrete FR decisions (e.g. the sibling-directory worktree location in FR-1 is justified by IDE/file-watcher concerns that follow from the Linux-laptop developer context).

The differentiation in §1 is earned, not innovation theater: it explicitly concedes "Claude Code now ships native `--worktree` creation" and positions against it as "the **control plane and dashboard** that native worktrees lack" — naming the baseline it competes with rather than pretending novelty.

### Findings
- **low** Two NFRs lean on adjective-substance ("snappy", "out of the way") (§ Cross-Cutting NFRs, NFR-Performance and NFR-Simplicity/Footprint) — "calls must stay snappy" and "the tool stays out of the way" are not by themselves testable, though NFR-Performance does also give a real bound ("sub-second for git CRUD"). *Fix:* lean on the numeric targets and drop or quantify the adjectival restatements.

## Strategic coherence — strong

The PRD has a clear thesis — agent *self-service* of worktree + task management plus a single monitoring glance — and the features serve it as an arc rather than a backlog. Worktree Management (§4.1), Task Tracking (§4.2), and the Dashboard (§4.3) are the three legs of that thesis; MCP Server (§4.4) and Persistence/Lifecycle (§4.5) are the enabling substrate. Nothing in §4 is orphaned from the thesis.

Success Metrics validate the thesis rather than measure activity. SM-1 ("doesn't abandon it after a month") tests genuine adoption rather than a vanity count; SM-2 ("can correctly state what every running agent is doing in under ~10 seconds") directly tests the "single glance" claim. Counter-metrics are present and well-chosen: SM-C1 (latency) and SM-C2 (tool-surface size) guard the exact failure modes that feature growth would create, and they are tied back to the NFRs and research IDs (R3, R4). The MVP scope reads as a problem-solving kind with scope logic that matches.

## Done-ness clarity — strong

This is the PRD's strongest dimension and the one downstream story creation leans on hardest. Every FR carries a "Consequences (testable)" block with verifiable conditions. Examples: FR-1 — "If the requested branch already exists or is checked out in another worktree, the tool refuses with a clear, structured error and makes no change"; FR-3 — "refuses to remove a worktree with uncommitted changes unless an explicit `force` flag is passed"; FR-12 — "A worktree deleted out-of-band ... is detected and no longer shown as active; its task is marked detached/closed." These are story-ready acceptance conditions.

The addendum §3 reinforces this with a capability→FR tool table giving input/output sketches per FR, so architecture has concrete shapes to refine.

### Findings
- **medium** FR-9 acceptance bound is deferred to a soft NFR target (§ 4.3 FR-9 / Cross-Cutting NFRs) — FR-9's consequence says the change appears "within a bounded refresh interval (see NFR Performance)", but NFR-Performance only commits to "a few seconds", and SM-2 separately implies a ~10s glance budget. An engineer cannot derive a single pass/fail threshold for refresh latency. *Fix:* commit one number (e.g. "visible within 3s") and reference it from FR-9, NFR-Performance, and SM-2 identically.
- **low** "make no change / leave the repo unchanged" is asserted but not given a verification handle (§ 4.1 FR-1, Constraints & Guardrails) — atomicity of a failed `git worktree add` is the kind of claim that needs a test, not just a statement. *Fix:* note the expected post-failure state explicitly (no branch, no directory) so it is testable.

## Scope honesty — strong

Omissions are explicit, not inferred. §5 Non-Goals does real work and is mirrored in §6.2 with a per-item "reason"; the `[NON-GOAL for MVP]` tag at §5 marks the two deferrals (same-file collision pre-warning, worktree bootstrap) that a reader might otherwise silently assume are in. De-scoping is proposed openly with reasons, never done silently.

Open-items density is well-calibrated to the stakes. The PRD carries 5 Open Questions (one already resolved), 5 indexed assumptions (one resolved), and 3 `[NOTE FOR PM]` callouts — appropriate for an internal tool that is a planning input, not a green-light-to-build artifact. Every `[ASSUMPTION]` is indexed in §9 and the index entries all trace to decision-log IDs.

### Findings
- **low** A2 is attached to two FRs without distinguishing the inference per site (§ 4.2 FR-4, §4.5 FR-12; indexed §9) — A2 covers "task/worktree state is persisted to local storage", but FR-4's use (the one-active-task-per-worktree conflict rule) is a behavioral assumption distinct from FR-12's persistence-medium assumption. Bundling them under one ID risks one being confirmed while the other is overlooked. *Fix:* split into A2 (persistence medium) and a separate assumption for the single-active-task invariant, or annotate the two call-sites distinctly.

## Downstream usability — strong

As a chain-top PRD this matters, and it holds up. The §3 Glossary is present and the domain nouns (Worktree, Branch, Agent, Task, Status, MCP tool, Dashboard, Server) are used verbatim across §4, §6, §7, and the addendum. FR / SM / UJ / NFR IDs are contiguous and unique (FR-1–13, SM-1–4 + SM-C1/C2, A1–A5), and cross-references resolve — e.g. FR-2's "(reconciled, not just an internal cache — see FR-12)" points to a real FR; the addendum's tool table maps each tool to a live FR.

The capability/implementation split is the standout enabler: §0 commits that "technology choices ... live in the companion `addendum.md`", and the addendum honors it (stack, transport, schemas, persistence), giving architecture a clean source-extraction surface without polluting the PRD's capability narrative.

### Findings
- **low** SM-3 cross-references a tool-call count without a glossary anchor for "single MCP tool call" (§ 7 SM-3) — minor; "single MCP tool call" is clear in context but the multi-step nature (create worktree *and* register task are two tools per UJ-1 step 1) could read as in tension. *Fix:* clarify SM-3 scopes to the worktree+branch creation call (FR-1) specifically, which it already cites.

## Shape fit — strong

The PRD correctly adopts a capability-spec shape for a single-operator internal tool. §0 states "Scope is calibrated to an **internal tool**", §2.3 down-scales to one representative UJ with explicit reasoning, and §7 uses qualitative-primary SMs ("operational rather than user-facing", per the rubric's internal-tool guidance) — SM-1 self-adoption and SM-2 glanceability are operator outcomes, not consumer funnel metrics. It is neither over-formalized (no UJ density padding) nor under-formalized (the one UJ that is load-bearing for the dashboard exists).

The "Developer-Product Surface" section is a smart shape adaptation: it recognizes that the MCP tool surface is a public contract ("the set of tool names ... is the public surface Claude Code depends on") and treats versioning stability accordingly — appropriate for a tool whose consumer is another agent.

## Mechanical notes

- **Glossary drift:** none material. Terms are used consistently in case and form across PRD and addendum. Minor: §1 introduces "control plane" as a positioning phrase not in the Glossary, but it is rhetorical, not a domain noun requiring a definition.
- **ID continuity:** clean. FR-1–13 contiguous; SM-1–4, SM-C1–C2 contiguous; A1–A5 contiguous; NFRs named not numbered (fine). No gaps or duplicates found.
- **Assumptions Index roundtrip:** complete. Inline `[ASSUMPTION: A1/A2/A3/A5]` tags all appear in §9; A4 correctly shown resolved both inline (none remaining) and in the index. A2 appears at two inline sites (FR-4, FR-12) and is indexed once — roundtrip holds, but see the Scope-honesty finding on splitting it.
- **UJ protagonist naming:** UJ-1 names its protagonist ("Dev") and carries context inline (Linux laptop, three-way feature split). No floating UJs.
- **Cross-refs:** spot-checked FR-12 (from FR-2, FR-3, FR-13, SM-4, NFR-Reliability), §5 (from FR-1, FR-3, FR-10), addendum tool table → FRs — all resolve.
- **Required sections:** all present for an internal-tool chain-top PRD (Vision, Target User, Glossary, Features/FRs, Non-Goals, MVP Scope, Success Metrics, Open Questions, Assumptions Index, Cross-Cutting NFRs, Constraints). The decision-log IDs (D*, R*, A*) are referenced throughout but the `.decision-log.md` itself was not provided for this review — assumed present per §0/§9 references.
