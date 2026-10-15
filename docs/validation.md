# Initial validation

Validated locally on 2026-10-15 with Python 3.12.14, uv 0.12.23, and n8n 2.43.5.

## Automated checks

- `uv sync --locked`: dependency installation succeeded.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed.
- `uv run pytest`: 36 passed; 1 PostgreSQL integration test skipped locally.
- `uv build`: source distribution and wheel built successfully.
- Freestyle SDK 0.2.20 installed from the frozen pnpm lockfile; provisioning script
  syntax checked and SDK option names checked against its installed type definitions.

## GitHub Actions verification

The first published source commit `cc0cf07381a5a31e12ba27eaf91fc79afb60945a`
passed [GitHub Actions CI](https://github.com/aboumelon/n8n-ci-triage/actions/runs/38715278277) on
2026-10-15. The run completed successfully with:

- **37 tests passed**, including the PostgreSQL concurrent ingestion and claim test.
- Ruff lint and formatting checks passed.
- `docker compose config --quiet` passed.
- `docker compose build triage` built the deployment image successfully.

## Real n8n integration check

A temporary n8n instance was initialized, an owner account was created, the header
credential and workflow were imported through the n8n CLI, and the workflow was
published. A real Python HTTP server received signed sample events and woke the real
n8n production webhook. The HTTP nodes executed the stage API through acknowledgement.

The only workflow change for this host-based check was replacing the Compose hostname
`triage:8000` with the test server's loopback address. No node logic or expressions were
changed. Both services used temporary SQLite databases for this check.

The `scripts/check_deployment.py` verifier was also run against these real services
using loopback URLs. Its first invocation verified a newly queued dependency run;
the second identified the duplicate as a saved report instead of claiming a new
execution. All five fixture scenarios subsequently completed. Separate checks
confirmed that the verifier refuses live GitHub or enabled Telegram delivery before
sending a demo. Public Freestyle HTTPS still requires the target-VM check.

| Scenario | Classification | Final run state |
|---|---|---|
| Test failure | `test` | `done` |
| Missing dependency | `dependency` | `done` |
| Lint failure | `lint` | `done` |
| Same dependency failure in a new run | `dependency` | `done` |
| Subsequent successful run | Recovery recorded | `done` |

Each original delivery was then sent again and returned `duplicate` without creating
another run. GitHub Issue timeout reconciliation was checked with a stateful fake that
accepts an Issue creation and then raises a simulated connection failure.

## Checks that still require deployment access

- The complete Compose deployment, including n8n owner setup and persistence across
  container restarts, still needs a smoke test on the target VM. Image building and
  the PostgreSQL queue integration test have already passed in GitHub CI.
- A Freestyle VM was not provisioned; no Freestyle account credential was available.
  Verify effective retention, HTTPS routing, pause/resume, and restart persistence there.
- No live GitHub Issue or Telegram message was sent. Those require the operator's
  account credentials and a reachable deployed gateway.
