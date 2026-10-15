# Live GitHub and Telegram setup

## GitHub access

Create a fine-grained personal access token restricted to the repositories you want to
monitor. The initial version uses a token; a GitHub App installation flow is planned.

| Repository permission | Access | Purpose |
|---|---|---|
| Metadata | Read | Repository access |
| Actions | Read | Read run attempts, jobs, and logs |
| Issues | Read and write | Create Issues, add occurrence comments, record recovery |

Issues write access is unnecessary while `PUBLISH_MODE=draft`. No repository contents
write permission, workflow write permission, or deployment permission is needed by the
running application. Save `GITHUB_TOKEN` in the server's private `.env`.

Set:

```dotenv
TRIAGE_MODE=live
PUBLISH_MODE=draft
ALLOWED_REPOSITORIES=aboumelon/n8n-ci-triage
```

Restart the triage container after editing environment values:

```bash
docker compose up -d triage
```

## Repository webhook

In the monitored repository, open **Settings → Webhooks → Add webhook**:

- Payload URL: `https://YOUR-HOOKS-HOST/webhooks/github`.
- Content type: `application/json`.
- Secret: the generated `WEBHOOK_SECRET` from the server's `.env`.
- Event selection: **Workflow runs** only.
- SSL verification: enabled.

The endpoint accepts signed `ping` requests and completed workflow runs with conclusion
`failure`, `timed_out`, or `success`. Other events are ignored. Authentication uses the
exact raw bytes, not a JSON re-serialization. Only allowlisted repositories enter the
inbox, and API URLs are constructed from validated identifiers instead of payload URLs.

Check Recent Deliveries for an HTTP `202`. Publication is asynchronous. A duplicate
delivery returns `202` with status `duplicate` and the original `run_key`.

## A real controlled failure

The repository includes a manual-only **Triage demo** workflow. From **Actions → Triage
demo → Run workflow**, select `dependency`, `test`, or `lint`. Its failure is deliberate.

1. Confirm a draft report appears in `python3 scripts/inspect_runs.py`.
2. Set `PUBLISH_MODE=github` and restart `triage`.
3. Trigger a new run. An Issue should be created with evidence and the original run link.
4. Trigger the same scenario again. It should add an occurrence comment to that Issue.
5. Select `success` to record workflow recovery. Issue closure stays manual by default.

Existing draft runs are not automatically republished when the mode changes. Trigger a
new run for the live publication check. `AUTO_CLOSE_ON_SUCCESS=true` enables closure
only for tracked, older incidents of that exact repository, workflow, and branch.

## Telegram

1. Create a bot with [BotFather](https://t.me/BotFather).
2. Start a private conversation with the bot, or add it to the destination group.
3. Determine the destination `chat_id` using Telegram's `getUpdates` method after sending
   a message. A group ID may be negative. Avoid publishing the response or token.
4. Set both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the server's private `.env`.
5. Restart `triage` and trigger a new CI run.

The bot only sends outbound `sendMessage` requests. No Telegram webhook or inbound bot
server is required. Messages are plain text, bounded to 3,800 characters, and include
the GitHub run link and Issue links. Without Telegram configuration, the same text is
saved as a draft in the report. GitHub and Telegram access must be allowed by the VM's
outbound network policy.

Telegram delivery is at least once under an ambiguous network failure: Telegram does
not provide an idempotency key for this method. The database records acknowledged sends
so ordinary stage retries do not resend an already recorded notification.

## Inspect and replay

`GET /internal/runs` returns the most recent 100 runs and reports. All internal endpoints
require the `X-Triage-Key` header. Use `scripts/inspect_runs.py` so the key stays out of
terminal arguments and command history.

A run with five expired processing leases moves to `dead`. After correcting the
configuration, send an authenticated `POST /internal/runs/RUN_KEY/retry`. This only
requeues dead-lettered runs; it preserves completed stages and their idempotency markers.

## Primary references

- [GitHub webhook events](https://docs.github.com/en/webhooks/webhook-events-and-payloads#workflow_run)
- [GitHub webhook validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [GitHub Actions jobs API](https://docs.github.com/en/rest/actions/workflow-jobs)
- [GitHub Actions run attempts API](https://docs.github.com/en/rest/actions/workflow-runs)
- [Telegram Bot API](https://core.telegram.org/bots/api#sendmessage)
