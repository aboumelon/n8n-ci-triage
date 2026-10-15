# Validation

Validated locally on 2026-10-15 with Python 3.12.14, uv 0.12.23, and n8n 2.43.5.

## Automated checks

- `uv sync --locked`: dependency installation succeeded.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed.
- `uv run pytest`: 133 passed; 2 PostgreSQL integration tests skipped locally.
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

The Telegram pairing helper at `ff08bf8577bd43221cd9f96407d20712bb20b01b` passed
[GitHub Actions CI](https://github.com/aboumelon/n8n-ci-triage/actions/runs/38748810230).
Its regression checks cover selection of the verified private chat among unrelated
chats, preservation of existing configuration after setup failures, private file
permissions, and token-safe API error messages. A separate local PTY check exercised
the real hidden token prompt and Enter prompt with simulated Telegram responses;
that local check did not send a live message.

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

## Live GitHub and Telegram verification

On 2026-10-15, the operator configured live GitHub access and enabled Issue
publication on the target VM. The following repository evidence was subsequently
read back through the GitHub API:

| Check | Evidence |
|---|---|
| First Issue publication | [Issue 3](https://github.com/aboumelon/n8n-ci-triage/issues/3), category `dependency`, links run `38743199205` |
| Repeated failure | [Occurrence comment](https://github.com/aboumelon/n8n-ci-triage/issues/3#issuecomment-6193523803) for run `38744994244` on the same Issue |
| Successful recovery | [Recovery comment](https://github.com/aboumelon/n8n-ci-triage/issues/3#issuecomment-6193552628) for successful run `38745185682` |
| Failure after Telegram setup | [Occurrence comment](https://github.com/aboumelon/n8n-ci-triage/issues/3#issuecomment-6194195838) for run `38749572663` |
| Recovery after Telegram setup | [Recovery comment](https://github.com/aboumelon/n8n-ci-triage/issues/3#issuecomment-6194441470) for run `38751301505`; the operator supplied the matching Telegram success message |
| Scenario after manually pausing the VM | [Occurrence comment](https://github.com/aboumelon/n8n-ci-triage/issues/3#issuecomment-6194777670) for run `38753633193`; the operator reported automatic return to `running` and successful processing |

After running the private-chat pairing helper and recreating `triage`, the operator
reported receiving the real CI notification in Telegram. The supplied message names
`aboumelon/n8n-ci-triage`, workflow `Triage demo`, branch `main`, job `controlled-demo`,
category `dependency`, and the intentionally missing dependency. It links run
`38749572663`, attempt 1, and Issue 3, matching the GitHub evidence above.

The operator subsequently supplied a `CI success` Telegram notification for the
same workflow and branch, linking run `38751301505`, attempt 1, and Issue 3. Its
matching GitHub recovery comment was independently read back through the API.

The operator then manually paused the Freestyle VM and launched another controlled
failure, run `38753633193`. They reported that the VM returned to `running` after
the scenario and that processing worked correctly. The matching failure comment
was independently read back from Issue 3. This is operator-observed wake-up from
a manually paused VM; it does not measure automatic idle pausing or wake-up latency.

This establishes live Issue publication, repeat-failure updates, workflow recovery
comments, and operator-reported Telegram failure and success delivery. Telegram
receipt and VM state were confirmed by the operator, rather than direct access
to their chat or VM control plane.
The intentionally failing demo is an acceptance test and does not indicate failure
of the project's normal CI.

## Remaining checks

- Verify persistence across a complete stack restart and a backup/restore exercise.
- Confirm the effective idle-timeout and retention settings and observe automatic
  idle pausing. Wake-up from a manual pause is already recorded above.
- The acceptance tests do not establish GitHub delivery deadlines, provider quota
  behavior, or sustained production availability.

## Optional LLM stage verification

On 2026-10-15, the ten-node workflow was imported and published in a fresh real
n8n 2.43.5 instance. A real local FastAPI server used temporary SQLite persistence;
its injected HTTP client simulated the remote model provider. The workflow's only
host adaptation was replacing `triage:8000` with the loopback API address.

| Scenario | Simulated model response | Saved LLM status | Final run state |
|---|---|---|---|
| Test failure | HTTP 429 quota error | `fallback` | `done` |
| Dependency failure | Valid structured explanation | `completed` | `done` |
| Lint failure | Malformed completion JSON | `fallback` | `done` |
| Repeated dependency failure | Valid structured explanation | `completed` | `done` |
| Successful run | No model call | `skipped` | `done` |

There were four simulated model calls for five runs. All publication and Telegram
results in this local check were drafts. Valid explanations appeared in saved Issue
Markdown and Telegram text. Duplicate webhook deliveries returned `duplicate`.
An initial wake-up arrived before n8n registered the production webhook; the durable
queue and scheduled recovery still completed that run.

The automated suite additionally covers disabled/no-evidence behavior, bounded and
redacted input, strict schema validation, mismatched finding indices, provider HTTP
errors, timeouts, oversized bodies, markup/mention filtering, safe error metadata,
cached results, immutable published reports, unchanged incident identity, publication
and recovery with an enriched or fallback report, and retained notification links.
The secret-entry helper has tests for private atomic updates, safe catalog errors,
exact model selection, and redirect refusal. An additional PostgreSQL test exercises
concurrent enrichment; it runs in GitHub CI when `TEST_DATABASE_URL` is provided.

These are implementation and orchestration checks with a simulated provider.
Successful Apinex inference, response quality, and live AI-enriched Issue/Telegram
delivery have not been verified. They require a new controlled failure after
resolving provider access using [the LLM setup guide](llm.md).

For live run `38768577720`, the operator supplied a saved report showing
`status=done`, model `free/deepseek-v4.1-flash`, and `llm.status=fallback` with
`reason=provider_http_error`, HTTP 402, and latency 1,226 ms. The operator also
supplied the ordinary Telegram failure notification, and the matching
[Issue occurrence comment](https://github.com/aboumelon/n8n-ci-triage/issues/3#issuecomment-6196922539)
was read back from GitHub. This verifies that a live model-provider refusal did not
block the report; it does not establish successful model generation.

## AMD TokenFactory compatibility checks

On 2026-10-15, the documented AMD shared API endpoint was checked without a key
and returned HTTP 401 with a JSON response. This establishes endpoint reachability
from the development environment, not authenticated inference or VM connectivity.

Local checks cover the AMD setup profile, exact shared endpoint matching, rejection
of missing/false/non-boolean free or JSON flags for unrecognised models, filtered
alternatives, preservation of existing service settings, private file permissions,
and secret-safe output. The tiny probe checks catalog membership before any AMD
completion. Nested AMD rate-limit errors are reduced to known codes.
Runtime tests exercise AMD's configured base URL,
model ID, system/user message shape, and JSON response validation with a simulated
provider. Radeon key-format redaction and omission of AMD-style reasoning fields
are also covered. Live AMD credentials, explanation quality, and delivery of an
AMD-generated report remain operator acceptance checks.

Operator screenshots subsequently showed no catalog entries matching both boolean
free/JSON flags, but the current AMD dashboard labelled `MiniCPM5-2B` **FREE**.
The exact shared endpoint and JSON support were also checked against AMD's current
[shared API guide](https://amd-aim.github.io/radeon-cloud-docs/guides/model-apis/)
and [model reference](https://amd-aim.github.io/radeon-cloud-docs/models/minicpm5-2b/).
The helper now permits that documented model profile despite conflicting metadata,
requires its live catalog membership, and tests actual JSON output before saving
any AMD settings. This exception does not apply to other models or endpoints.
Simulated checks cover a successful JSON probe after metadata disagreement, quota
and unsupported-format errors, timeouts, non-JSON output, missing catalog membership,
truncated completions, tool calls, and strict validation of the tiny answer.
Failed probes preserve the previous file without printing credentials or provider
content. These are simulated provider checks; a successful authenticated AMD probe
and a full AI-enriched report remain unverified at this point.
