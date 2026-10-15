# Optional LLM explanations

The Python service can call an OpenAI-compatible Chat Completions API after
deterministic analysis and before publication. The model adds an explanation,
possible causes, and investigation steps. n8n orchestrates the separate
`Explain findings (LLM)` HTTP node. No model weights or inference runtime are
installed on the VM, and no additional Python dependencies are needed.

The default configuration is disabled. Existing workflows without this node can
continue processing during an upgrade. New executions use the imported workflow.

## Configure AMD TokenFactory

The AMD Radeon Cloud Public Free Model APIs provide shared OpenAI-compatible
endpoints for evaluation, development, and demos. This fits this portfolio project.
The service has quotas and is not offered for production workloads; see its
[API terms](https://amd-aim.github.io/radeon-cloud-docs/api/terms/).

1. Sign in at [TokenFactory](https://developer.amd.com.cn/radeon/tokenfactory).
2. Under **Public Free Model APIs**, open a model detail dialog and copy your API
   key. Radeon keys start with `rc-`; the same key works across shared models.
   Accounts pending verification may not have a key yet.
3. In the VM repository directory, configure the AMD profile:

```bash
git pull --ff-only
python3 scripts/configure_llm.py --provider amd
python3 scripts/check_llm.py
```

Enter the Radeon key at the hidden prompt. The AMD profile selects
`https://developer.amd.com.cn/radeon/api/v1` and `MiniCPM5-2B`, a small text model
documented to support JSON output and answer without separated thinking. Its
provider stability label is experimental; explanation quality still needs the
operator's acceptance test. The helper verifies the exact live catalog entry and
requires both `free=true` and `json_output=true` before saving anything. If the
model is unavailable, it lists only free, JSON-capable alternatives. Choose one
explicitly, for example:

```bash
python3 scripts/configure_llm.py --provider amd --model DeepSeek-V4-Flash
```

The helper preserves GitHub/Telegram settings, sets the HTTP timeout to 30 seconds,
and never launches a GPU instance or chooses a dedicated endpoint. The existing
Python client already speaks the required chat/JSON format. No new n8n credential,
node, or workflow import is needed when the explanation stage is already published.

After the tiny probe is accepted, rebuild/recreate the Python service:

```bash
sudo docker compose up -d --build --wait triage
```

Run a new controlled failure and inspect `llm.status`, the Issue comment, and the
Telegram summary. An accepted tiny probe does not establish a valid full explanation.
The model runs remotely at AMD; the Freestyle VM only calls its API. No model weights
or GPU runtime are installed on the VM.

The AMD probe checks the live free/JSON catalog before sending its single tiny
completion. Nested rate-limit errors are reduced to recognized codes without raw
messages. Shared capacity and per-account quotas can vary. The normal fallback
still applies to timeouts, 429, provider failures, or invalid model output.

References: [Model APIs](https://amd-aim.github.io/radeon-cloud-docs/guides/model-apis/),
[catalog](https://amd-aim.github.io/radeon-cloud-docs/api/models/),
[authentication](https://amd-aim.github.io/radeon-cloud-docs/api/authentication/), and
[MiniCPM5-2B](https://amd-aim.github.io/radeon-cloud-docs/models/minicpm5-2b/), reviewed
on 2026-10-15. Authenticated AMD inference has not yet been verified for this project.

## Configure Apinex

Create an account and API key yourself at [Apinex](https://apinex.bond/keys).
The provider documents the base URL `https://api.apinex.bond/v1` and bearer-token
authentication. Its public catalog advertises `free/deepseek-v4.1-flash`; this is
a provider alias, not an independent verification of the underlying model.
Availability, free quotas, and provider policies can change. Check your account's
current allowance; no particular free token quota is assumed by this application.

From the repository directory on the VM:

```bash
git pull --ff-only
python3 scripts/configure_llm.py
```

Paste the API key at the hidden prompt. It is never a command-line argument or
printed output. The helper checks the authenticated `/models` catalog, requires
the exact selected free alias, then atomically updates `.env` with mode `0600`.
Other settings, including GitHub and Telegram credentials, are preserved. Catalog
access verifies the key and listed model; it does not establish successful inference
or remaining quota. If the default alias is unavailable, the helper lists available
free aliases and leaves `.env` unchanged. Select one explicitly:

```bash
python3 scripts/configure_llm.py --model free/glm-5.3-flash
```

If setup returns HTTP 403 before saving anything, first update the checkout and retry:

```bash
git pull --ff-only
python3 scripts/configure_llm.py
```

The setup helper and inference client identify themselves as `n8n-ci-triage/0.1`
and request JSON. A provider edge rejected the default Python urllib client with
HTTP 403 / Cloudflare 1010 during an unauthenticated connectivity check; an explicit
application User-Agent reached the API's normal missing-key response instead.
This does not establish that an operator's key or VM IP is authorized. The helper
recognizes plain-text/HTML access denials and Cloudflare 1010 JSON error documents
without displaying response bodies or secrets. A persistent web denial needs
provider-side review of the VM's access; an API authentication error needs review
of the key and account permissions.

If a completed triage report has `llm.status=fallback` and `http_status=402`,
the workflow reached the provider but its request was refused. Apinex documents
402 as insufficient spendable balance or API-key allowance for the request's
temporary token reservation. A free alias appearing in `/models` does not establish
that the account or key has usable allowance for inference.

Run a single small diagnostic request, using the key already saved in `.env`:

```bash
python3 scripts/check_llm.py
```

This sends only `Reply only OK.` with a 32-token output limit to the configured
free model. It sends no CI data, performs no retries or model switches, changes
no settings, and prints only status and recognized error codes. The request may use
a small amount of the provider's free allowance. It never prints the API key, raw
error messages, reasoning, or model-generated text.

If this small request also gets 402, review the account's remaining free allowance
and this API key's spending limit, or ask provider support about free-model access.
Changing n8n or repeating CI scenarios cannot resolve that allowance refusal.
If the small request is accepted, check the larger request's reservation limit;
reducing `LLM_MAX_OUTPUT_TOKENS` may help but can also truncate the required JSON.
An accepted probe is not proof that a complete triage explanation will validate.
After resolving access, run a new CI scenario because published reports are cached.

For Apinex and custom endpoints, the helper only accepts `free/` aliases. On AMD's
exact shared endpoint it accepts plain model IDs after live catalog verification.
Direct configuration supports other OpenAI-compatible providers with these settings:

```dotenv
LLM_ENABLED=false
LLM_API_KEY=
LLM_BASE_URL=https://api.apinex.bond/v1
LLM_MODEL=free/deepseek-v4.1-flash
LLM_TIMEOUT_SECONDS=15
LLM_MAX_OUTPUT_TOKENS=1200
```

`LLM_BASE_URL` must use HTTPS without credentials, query parameters, or fragments.
The service sends one non-streaming POST to `/chat/completions` and requests JSON
output. HTTP errors (including unsupported response-format parameters), timeouts,
oversized responses, and invalid JSON all produce a saved fallback. There are no
automatic provider retries or model switches. To disable calls, set
`LLM_ENABLED=false` and recreate the triage container.

## Upgrade the running Compose stack

After configuring the key, run the following from the VM repository directory.
Use `sudo` where Docker requires it:

```bash
sudo docker compose up -d --build --wait triage
sudo python3 scripts/bootstrap_n8n.py
sudo docker compose exec -T --interactive=false -u node n8n \
  n8n publish:workflow --id=ciTriageWorkflowV1
sudo docker compose restart n8n </dev/null
```

The bootstrap updates this project's existing workflow and header credential by
their stable IDs. It overwrites local editor changes to that workflow, so export
any custom version first. The database, n8n owner account, incident history, and
existing service credentials are retained. The schema does not change. The API
key belongs only in Python's `.env`; no new n8n credential is needed.

Trigger a **new** `dependency`, `test`, or `lint` scenario in **Triage demo** on
GitHub Actions. Re-delivering a completed run uses its cached report. A successful
run skips the model because it has no failed-job evidence.

Inspect the newest run's explanation metadata without displaying credentials:

```bash
python3 scripts/inspect_runs.py | python3 -c '
import json, sys
for run in json.load(sys.stdin)["runs"][:3]:
    report = run.get("report") or {}
    print(json.dumps({"run_id": run["run_id"], "status": run["status"],
                      "llm": report.get("llm")}, indent=2))
'
```

Expect `llm.status=completed`, an `AI-assisted investigation` section in the Issue
or occurrence comment, and `AI summary` in the Telegram notification. A repeated
fingerprint updates its existing Issue. `fallback` still publishes and notifies
using deterministic evidence. Its safe `reason` and optional HTTP status distinguish
authentication/quota/provider failures from a malformed response. `disabled` means
the feature was off; `skipped` means no failure evidence was eligible. A missing
`llm` field on a new run usually means the old workflow is still published.

## Data and execution boundaries

- Send at most three available findings, each containing category, job and step names
  capped at 200 characters, evidence capped at 1,000 characters, and a truncation flag.
  Repository names, branch names, URLs, fingerprints, raw logs, source files, and
  credentials are not included as separate fields. Evidence itself may contain
  project details. Configured secrets and known credential patterns are redacted;
  this cannot prove that arbitrary text contains no sensitive data. Use the public
  portfolio demo unless the remote provider is approved for private project data.
- Treat all evidence as untrusted input. The system prompt requires JSON and
  evidence-grounded hypotheses, but prompting cannot guarantee factual accuracy or
  defeat every injection. There are no model tools, command execution, or repository
  edits. Classification and incident identity are never taken from model output.
- Validate strict local schemas: no extra fields, bounded strings and lists, and
  exactly one unique result per requested finding index. Truncated completions and
  tool calls are rejected. Redact output again, omit model-provided URLs, and escape
  Markdown, HTML, and mentions before adding advice to an Issue.
- Request at most 1,200 output tokens by default, accept at most 64 KiB of decoded
  response, and configure 15-second HTTP operation timeouts plus an elapsed-time
  check between received chunks. These are safeguards, not a provider billing or
  hard wall-clock guarantee. No paid alias is automatically substituted.
- Save status, prompt version, configured model, latency, validated advice, and any
  valid token counts reported by the provider. Do not save raw provider responses,
  reasoning fields, provider error bodies, or the API key. There is no external
  prompt tracing service.
- Cache the whole outcome, including fallback, in the existing report transaction.
  Stage retries reuse that committed result. A process crash after the provider
  accepts a request but before commit can cause another call after lease recovery;
  this is not exactly-once API billing. Already published reports are immutable.

Telegram messages remain bounded and reserve space for the run URL and up to five
unique Issue URLs. Full per-finding advice remains available in GitHub reports and
the saved report.

## Validation scope

Local tests simulate valid completions, provider authentication/quota/server errors,
timeouts, oversized bodies, schema violations, secret exposure, and markup injection.
They verify that classification/fingerprints remain stable and that publication,
notification, and recovery still complete during fallback. Provider inference quality,
actual free quota, and live delivery of AI-enriched reports require the operator's
authenticated acceptance run; see [validation.md](validation.md).

Provider references: [Chat Completions](https://apinex.bond/developers/models/chat),
[model catalog](https://apinex.bond/developers/models), and
[public pricing](https://apinex.bond/pricing), reviewed on 2026-10-15.
See also [provider errors and limits](https://apinex.bond/developers/errors).
