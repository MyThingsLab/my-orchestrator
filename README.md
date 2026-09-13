# my-orchestrator

[![CI](https://github.com/MyThingsLab/my-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-orchestrator/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/MyThingsLab/my-orchestrator/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-orchestrator) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Picks the single next unit of work across the whole [MyThingsLab](../my-things-core)
fleet, for the one available worker.

"Which of N designed tools / open issues should be tackled next" is a
recurring decision. `myorchestrator next` replaces it with a live,
re-computable answer — prioritized **deterministically** wherever possible.

## How it works

Deterministic pre-work, in order:

1. List every repo under the `MyThingsLab` org.
2. Collect candidates: open issues carrying each repo's backlog label, plus
   "scaffold this tool" candidates for designed-but-unbuilt tools (from the
   canonical fleet registry, `tools_manifest.json`, shipped as `mythings`
   package data in my-things-core; override with `--manifest`).
3. Keep only **ready** scaffolds — every `depends_on` entry satisfied (a
   depended-on tool already built, or a core-contract addition landed).
4. Filter on the CAD labels, before any ranking: `state:blocked`, `size:L` (too
   large to dispatch — it needs my-architect to decompose it first), and a
   missing `lane` or `size` are each excluded and **reported**, never defaulted
   and never silently skipped.
5. Rank with `mythings.labels.sort_key` — `(not critical, lane, prio, -age,
   repo, number)`, lexicographic and constant-free. Ledger urgency signals (an
   unresolved `kind=drift`, a `kind=ask` awaiting a reply) and MyPlanner's
   horizon boosts are reported alongside each candidate but no longer reorder
   it: an inferred score does not outvote a label a human set.
6. Only a genuine tie reaches the **one** optional Engine call — "choose which
   the worker tackles next, and why." A tie means equal on everything but the
   `(repo, number)` tail, which exists solely to make the order total. Against
   `NoopEngine` it falls back to that tail.

Its one side effect is updating a single pinned "next up" tracking issue via
`gh issue edit`, routed through `Action` → `Policy`. It decides; it never builds
and never chains into another tool's CLI.

## Usage

```bash
myorchestrator next            # human-readable
myorchestrator next --json     # machine-readable
```

## In the fleet loop

`myorchestrator` is never invoked directly by another tool's CLI — the org
root's [`fleet_dispatch.py`](../fleet_dispatch.py) imports `Orchestrator` as a
library to rank candidates and hand them to workers, and
[`fleet_cycle.py`](../fleet_cycle.py) chains it with the rest of the fleet
(`myplanner` → `fleet_dispatch` → `mytester`/`mychangelogger` →
`myprojector` → `myreporter` → `mytelegrambot`) into one autonomous cycle. See
the [org README](../README.md) for the full loop.

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../my-things-core -e ".[dev]"
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
