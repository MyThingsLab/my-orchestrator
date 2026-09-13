# my-orchestrator — agent instructions

You are developing **my-orchestrator**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `my-things-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** reads every backlog across the fleet — open issues per repo, and
  not-yet-scaffolded tools from the `my-things-core/docs/tools/` designs — and
  produces the single next unit of work for the one available worker,
  prioritized deterministically wherever possible.
- **The single Engine call:** optional and narrow — "given N
  deterministically-tied top candidates, choose which the single available
  worker should tackle next, and say why." Input: the tied candidates'
  summaries, `context={"tie_count": k}`. Output: `data={"chosen": candidate_id,
  "reason": str}`. Most runs never reach it (a single top candidate skips it);
  it only fires on a genuine tie among top candidates — equal on everything in
  `sort_key` but its `(repo, number)` tail. Against `NoopEngine` it falls back
  to that tail.
- **Invariants / rules:** decides, never builds — no `Workspace`, no code PR.
  Its one side effect is updating a single pinned "next up" tracking issue via
  `gh issue edit`, routed through `Action(kind="bash", ...)` → `Policy`
  (`ALLOW` by default). Never invokes another tool's CLI directly — it
  recommends; the worker acts on it as a separate run. Ranking is deterministic
  and belongs to the fleet, not to this tool: `mythings.labels.sort_key` over
  the CAD labels, preceded by that module's `validate()` as a hard filter.
  Ledger/planner urgency is reported, never ranked on. The Engine only breaks a
  genuine tie. Fleet-wide by default (reads across every repo under the org),
  not opt-in per-repo.
- **Backlog label:** none of its own — it *is* the thing that reads every other
  tool's label.
