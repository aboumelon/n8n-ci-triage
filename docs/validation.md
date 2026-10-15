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

## Real n8n integration check

A temporary n8n instance was initialized, an owner account was created, the header
credential and workflow were imported through the n8n CLI, and the workflow was
published. A real Python HTTP server received signed sample events and woke the real
n8n production webhook. The HTTP nodes executed the stage API through acknowledgement.

The only workflow change for this host-based check was replacing the Compose hostname
`triage:8000` with the test server's loopback address. No node logic or expressions were
changed. Both services used temporary SQLite databases for this check.

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

- Docker Engine is unavailable in the authoring environment, so the complete Compose
  stack and container build were not run locally. The GitHub CI configuration includes
  Compose validation and the image build.
- Native PostgreSQL could not start in the authoring environment because it provides
  only UID 0, while PostgreSQL requires a non-root runtime user. The CI configuration
  supplies PostgreSQL and runs the concurrent ingestion/claim integration test.
- A Freestyle VM was not provisioned; no Freestyle account credential was available.
  Verify effective retention, HTTPS routing, pause/resume, and restart persistence there.
- No live GitHub Issue or Telegram message was sent. Those require the operator's
  account credentials and a reachable deployed gateway.
- No remote GitHub CI result exists until the repository is created and the source
  is pushed. Local test success must not be presented as a green GitHub Actions run.
