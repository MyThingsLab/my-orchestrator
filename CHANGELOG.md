# Changelog

## [Unreleased]
### Added/Changed
- shipped MyOrchestrator skeleton: fleet-wide next-work picker, deterministic ranking + one optional tie-break Engine call, green ruff+pytest locally
- wired --engine {noop,claude-cli} into myorchestrator next, mirroring my-reporter/my-tester/my-guard's pattern (default noop unchanged)
- Add --engine-model flag: build_engine(name, model) factory (noop->None to preserve the deterministic tie-break fallback) so --engine claude-cli can pick a model (issue #6)
- wire MyPlanner kind=plan signal into ranking (horizon boosts + pause-new-tools penalty); add my-projector/my-planner to manifest
### Fixed
- CI was red only because mythings-core's main was stale (design/next-tools + MBAI-naming fix hadn't merged yet); now merged, re-ran the same commit's CI and it passed, no code change needed
- found while wiring --engine claude-cli: _break_tie read result.data.get('chosen'), but ClaudeCLIEngine's .data is the raw CLI JSON envelope (type/is_error/result/...), never a {chosen,reason} shape -- the tie-break has silently never worked against a real engine, only the mocked SpyEngine tests (which hand-craft data directly) ever exercised it. Fixed to parse the model's own reply (EngineResult.text) as JSON, with the system prompt now explicitly asking for that JSON shape. Live-verified: a real tie now resolves with genuine model-generated reasoning instead of always falling back to oldest-first
