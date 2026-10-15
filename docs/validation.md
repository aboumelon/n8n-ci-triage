# Validation

Validated locally on 2026-10-15 with Python 3.12.14, uv 0.12.23, and n8n 2.43.5.

## Automated checks

- `uv sync --locked`: dependency installation succeeded.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed.
- `uv run pytest`: 39 passed; 1 PostgreSQL integration test skipped locally.
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

The deployment reset fix at `ad59a1ee4dd265330128aed3784737a1e4c925d1` also passed
[GitHub Actions CI](https://github.com/aboumelon/n8n-ci-triage/actions/runs/38730491291),
including the additional deployment-verifier regression tests.

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
sending a demo. The public Freestyle check is recorded separately below.

The deployment verifier also has regression tests using an HTTP server that resets
its TCP connection. Readiness recovers, a webhook retry preserves the same delivery
and body after an ambiguous response, and HTTP 401 is not retried.

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

## Freestyle demo verification

On 2026-10-15, the operator ran the deployment commands in an existing Freestyle VM
with 2 vCPU, 4 GiB RAM, and 16 GB storage. Operator-supplied terminal screenshots
confirm n8n credential/workflow import and publication, followed by this check after
restarting the n8n container:

```bash
python3 scripts/check_deployment.py \
  --gateway-url https://aboumelon-ci-65a149-hooks.style.dev
```

The final screenshot shows `PASS: gateway -> n8n -> completed dependency report`,
run status `done`, category `dependency`, and evidence
`ModuleNotFoundError: No module named 'httpx'`. The Issue action is a draft creation
with `issue_url: null`, and Telegram mode is `draft`.

This is operator-run evidence for the public HTTPS gateway, the published n8n stage
workflow, and a saved demo report on the target VM. It does not establish delivery
to a real GitHub repository or Telegram chat. The gateway is
`https://aboumelon-ci-65a149-hooks.style.dev/webhooks/github`; the n8n editor is
`https://aboumelon-ci-65a149-n8n.style.dev` and requires the operator's account.

## Remaining checks

- Verify persistence across a complete stack restart and a backup/restore exercise.
- Verify effective VM retention, automatic wake-up, and pause/resume with the editor
  closed. The demo smoke test does not measure GitHub delivery deadlines or quotas.
- No live GitHub Issue or Telegram message was sent. Those require the operator's
  account credentials and a reachable deployed gateway.
