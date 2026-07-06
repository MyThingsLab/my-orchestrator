from __future__ import annotations

import argparse
import json
from pathlib import Path

from mythings.ledger import Ledger

from myorchestrator.manifest import default_manifest_path
from myorchestrator.orchestrator import Orchestrator, Recommendation, Tracking


def _render(rec: Recommendation, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {
                "chosen": rec.chosen.id if rec.chosen else None,
                "reason": rec.reason,
                "engine_used": rec.engine_used,
                "candidates": [c.id for c in rec.candidates],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    if rec.chosen is None:
        return "next: (none) — no ready candidates"
    via = "engine tie-break" if rec.engine_used else "deterministic"
    return f"next: {rec.chosen.id}  [{via}]\n  {rec.chosen.title}\n  why: {rec.reason}"


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

    args = parser.parse_args(argv)
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
    )
    print(_render(orch.next(), as_json=args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
