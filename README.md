# my-orchestrator

Picks the single next unit of work across the whole [MyThingsLab](../mythings-core)
fleet, for the one available worker.

While only the interactive session can run judgment steps (no real `Engine`
backend yet), "which of N designed tools / open issues should be tackled next"
is a recurring manual decision. `myorchestrator next` replaces it with a live,
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

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../mythings-core -e ".[dev]"
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
