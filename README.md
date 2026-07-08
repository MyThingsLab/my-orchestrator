# my-orchestrator

[![CI](https://github.com/MyThingsLab/my-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-orchestrator/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/MyThingsLab/my-orchestrator/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-orchestrator) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Picks the single next unit of work across the whole [MyThingsLab](../mythings-core)
fleet, for the one available worker.

"Which of N designed tools / open issues should be tackled next" is a
recurring decision. `myorchestrator next` replaces it with a live,
re-computable answer — prioritized **deterministically** wherever possible.

## How it works

Deterministic pre-work, in order:

1. List every repo under the `MyThingsLab` org.
2. Collect candidates: open issues carrying each repo's backlog label, plus
   "scaffold this tool" candidates for designed-but-unbuilt tools (from
   [`manifest.json`](src/myorchestrator/manifest.json)).
3. Keep only **ready** scaffolds — every `depends_on` entry satisfied (a
   depended-on tool already built, or a core-contract addition landed).
4. Rank: strict oldest-first, boosted by live ledger urgency signals (an
   unresolved `kind=drift` / a `kind=ask` awaiting a reply jumps the queue).
5. Only a genuine tie among top candidates (same age, same urgency) reaches the
   **one** optional Engine call — "choose which the worker tackles next, and
   why." Against `NoopEngine` it falls back to strict oldest-first.

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
pip install -e ../mythings-core -e ".[dev]"
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
