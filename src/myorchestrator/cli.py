from __future__ import annotations

import argparse
import json
from pathlib import Path

from mythings.engine import ClaudeCLIEngine, Engine
from mythings.ledger import Ledger

from myorchestrator.manifest import default_manifest_path
from myorchestrator.orchestrator import Orchestrator, Recommendation, Tracking

_ENGINE_NAMES = ("noop", "claude-cli")


def build_engine(name: str, *, model: str | None = None) -> Engine | None:
    # noop -> None so the Orchestrator runs its deterministic tie-break with no
    # engine at all (its documented fallback), rather than a no-op reply.
    if name == "claude-cli":
        return ClaudeCLIEngine(model=model)
    return None


def _as_dict(rec: Recommendation) -> dict:
    return {
        "chosen": rec.chosen.id if rec.chosen else None,
        "reason": rec.reason,
        "engine_used": rec.engine_used,
        "candidates": [c.id for c in rec.candidates],
    }


def _render(rec: Recommendation, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(_as_dict(rec), separators=(",", ":"), sort_keys=True)
    if rec.chosen is None:
        return "next: (none) — no ready candidates"
    via = "engine tie-break" if rec.engine_used else "deterministic"
    return f"next: {rec.chosen.id}  [{via}]\n  {rec.chosen.title}\n  why: {rec.reason}"


def _render_many(recs: list[Recommendation], *, as_json: bool) -> str:
    if as_json:
        return json.dumps([_as_dict(r) for r in recs], separators=(",", ":"), sort_keys=True)
    lines = []
    for i, rec in enumerate(recs):
        if rec.chosen is None:
            lines.append(f"worker {i + 1}: (none) — no ready candidates")
        else:
            lines.append(f"worker {i + 1}: {rec.chosen.id}\n  {rec.chosen.title}\n  why: {rec.reason}")  # noqa: E501
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="myorchestrator",
        description="Pick the single next unit of work across the whole MyThingsLab fleet.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    nxt = sub.add_parser("next", help="report the single next unit of work")
    nxt.add_argument("--json", action="store_true", help="machine-readable output")
    nxt.add_argument("--org", default="MyThingsLab")
    nxt.add_argument("--manifest", type=Path, default=default_manifest_path())
    nxt.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd().parent,
        help="directory holding each repo as a subdir (defaults to the workspace root)",
    )
    nxt.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    nxt.add_argument("--tracking-repo", help='the "next up" pinned issue repo, e.g. MyThingsLab/mythings-core')  # noqa: E501
    nxt.add_argument("--tracking-issue", type=int, help="the pinned issue number to update")
    nxt.add_argument(
        "--count",
        type=int,
        default=1,
        help="number of concurrently available workers to pick distinct candidates for",
    )
    nxt.add_argument(
        "--engine",
        choices=sorted(_ENGINE_NAMES),
        default="noop",
        help="Engine backend for the tie-break (default: noop — falls back to oldest-first)",
    )
    nxt.add_argument(
        "--engine-model",
        help="model for --engine claude-cli (default: the CLI's own default; ignored by noop)",
    )

    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be >= 1")
    tracking = (
        Tracking(repo=args.tracking_repo, issue=args.tracking_issue)
        if args.tracking_repo and args.tracking_issue
        else None
    )
    orch = Orchestrator(
        org=args.org,
        manifest_path=args.manifest,
        repo_root=args.repo_root,
        ledger=Ledger(args.ledger),
        tracking=tracking,
        engine=build_engine(args.engine, model=args.engine_model),
    )
    if args.count == 1:
        print(_render(orch.next(), as_json=args.json))
    else:
        print(_render_many(orch.next_n(args.count), as_json=args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
