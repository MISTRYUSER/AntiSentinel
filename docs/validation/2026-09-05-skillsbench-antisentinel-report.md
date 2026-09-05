# SkillsBench AntiSentinel evaluation

## Scope

- Source: user supplied `skillsbench-main.zip`, SHA256 `2717a281b7c8a6ddc3450d24441071b59790c12f7e875a7a67b4a0151320b8a7`.
- Dataset inventory: 87 official local candidates, 23 single Skill tasks and 64 multi Skill tasks (`docs/validation/skillsbench-screening.json`).
- Agent: AntiSentinel session-factory ACP adapter with Docker task sandbox, DeepSeek `deepseek-chat`, preloaded task Skills, bounded command/path access, adaptive tool loop, duplicate-call feedback, and 16-turn/48-call benchmark budget.

## Adapter validation

The real `dialogue-parser` task completed through the full BenchFlow path and passed the official verifier: `reward=1.0`, 6/6 checks. The successful run is archived at `/tmp/antisentinel-skillsbench-user-20260905/skillsbench-main/jobs/antisentinel-dialogue-v10/2026-09-05__17-04-17`.

The integration fixes validated by this run are: absolute paths bound to `/app`, 60KB bounded tool-result summaries, cumulative tool-result history, distinct-call continuation after duplicates, non-empty Skill IDs, and a benchmark-specific expanded turn budget.

## Full cohort run

The complete 87-task with-Skill run is launched with concurrency 4 and build concurrency 2 under `jobs/antisentinel-full-v1`. It is intentionally retained as a live external run because Docker image pulls and task dependency installation are environment-bound and can take minutes per task. The report must be updated from its final `summary.json` before publishing aggregate scores; partial results must not be presented as a completed cohort score.

At the last audit, four task result files had been produced. Several tasks were still active in Docker setup or verifier execution. Observed environment failures from earlier official runs include Docker Hub pull timeouts, astral.sh TLS failures while installing `uv`, and tasks requiring unavailable stateful environments; these are recorded as infrastructure outcomes rather than model scores.

## Related formal evidence

- BFCL frozen subset: 15 cases, formal v1 scored 7/10 (70%) with 5 stateful multi-turn cases explicitly unsupported; optimized v2 scored 8/10 (80%) on the same scored subset. See `docs/validation/2026-09-05-bfcl-derived-report.md`.
- Local multi Skill A/B/C: 9/9 cases passed in `/tmp/antisentinel-multiskill-abc-r3`.
- Real double Skill smoke case completed with ordered active Skills `multi/diagnosis` then `multi/runbook`; see `/tmp/antisentinel-multiskill-r2/report.json`.

## Acceptance status

Adapter code and regression tests are complete (`363 passed`). The final SkillsBench aggregate and single/multi cohort tables remain pending the terminal full-run summary and must be filled in before PRD-004 is marked complete.
