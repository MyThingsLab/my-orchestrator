from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from mythings.engine import Engine, EngineRequest
from mythings.github import GitHub, Runner, _gh
from mythings.isolation import in_github_actions
from mythings.policy import Action, Decision, Policy

# Convention: an optional, human-curated per-repo doc of known problems not yet
# filed as issues -- distinct from the org-wide TODO.md roll-up. Missing file
# means the repo hasn't opted in; assess() is then a clean no-op, not an error.
ASSESSMENT_FILENAME = "ASSESSMENT.md"

# Every issue this mines gets this label, so a later run's dedupe check only
# has to look at issues *this* tool filed, not the whole open-issue set (same
# convention my-archivist uses for its bibliography-catalog issues).
ASSESSED_LABEL = "assessed"

_ENGINE_SYSTEM = (
    "Given a repo's curated list of known problems and the titles of issues "
    "already open for it, propose fresh, single-owner, right-sized issues for "
    "problems not yet tracked. Do not repeat anything in existing_titles. Reply "
    'with only a JSON object: {"issues": [{"title": "<title>", "body": "<body>"}]}, '
    "nothing else."
)


@dataclass(frozen=True)
class AssessResult:
    created: list[dict] = field(default_factory=list)  # {"title", "issue", "url"}
    skipped: list[dict] = field(default_factory=list)  # {"title", "reason"}
    engine_used: bool = False
    reason: str = ""  # set when there was nothing to do at all (e.g. no doc)


def read_assessment_doc(repo_root: str | Path, repo: str) -> str | None:
    path = Path(repo_root) / repo / ASSESSMENT_FILENAME
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def assess(
    *,
    org: str,
    repo: str,
    repo_root: str | Path,
    engine: Engine,
    policy: Policy,
    runner: Runner = _gh,
    max_new: int = 5,
) -> AssessResult:
    doc = read_assessment_doc(repo_root, repo)
    if doc is None:
        return AssessResult(reason=f"no {ASSESSMENT_FILENAME}")

    full_repo = f"{org}/{repo}"
    gh = GitHub(full_repo, runner=runner)
    existing_titles = {
        i.title for i in gh.list_issues(labels=[ASSESSED_LABEL], state="open", limit=100)
    }

    result = engine.run(
        EngineRequest(
            prompt=json.dumps(
                {"assessment": doc, "existing_titles": sorted(existing_titles)},
                separators=(",", ":"),
            ),
            system=_ENGINE_SYSTEM,
            context={"repo": repo},
        )
    )
    proposed = _parse_issues(result.text)
    if proposed is None:
        # NoopEngine / unusable reply: nothing filed. Unlike MyPlanner's
        # placeholder recommendation (harmless human-readable text), a
        # placeholder here would be a real, wrong GitHub issue.
        return AssessResult(engine_used=False, reason="engine gave no usable proposals")

    skipped: list[dict] = []
    survivors = []
    for item in proposed:
        if item["title"] in existing_titles:
            skipped.append({"title": item["title"], "reason": "already open"})
        else:
            survivors.append(item)

    to_file, overflow = survivors[:max_new], survivors[max_new:]
    for item in overflow:
        skipped.append({"title": item["title"], "reason": "max_new cap reached"})

    created: list[dict] = []
    for item in to_file:
        action = Action(kind="issue-create", payload={"repo": full_repo, "title": item["title"]})
        decision = policy.evaluate(action).under(unattended=in_github_actions())
        if decision is not Decision.ALLOW:
            skipped.append({"title": item["title"], "reason": f"policy: {decision.value}"})
            continue
        issue = gh.create_issue(title=item["title"], body=item["body"])
        gh.add_labels(issue.number, [ASSESSED_LABEL])
        created.append({"title": item["title"], "issue": issue.number, "url": issue.url})

    return AssessResult(created=created, skipped=skipped, engine_used=True)


def _parse_issues(text: str) -> list[dict] | None:
    try:
        obj = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return None
    issues = obj.get("issues")
    if not isinstance(issues, list) or not issues:
        return None
    clean = []
    for it in issues:
        if not isinstance(it, dict):
            return None
        title, body = it.get("title"), it.get("body")
        if not isinstance(title, str) or not title:
            return None
        clean.append({"title": title, "body": str(body) if body is not None else ""})
    return clean
