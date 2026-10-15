import json

import httpx
import sqlalchemy as sa

from .analysis import analyze_log, markdown_report
from .db import Conflict, incidents
from .github import UpstreamError
from .llm import Explainer, explanation_markdown


class TriageService:
    def __init__(self, settings, db, github, explainer=None):
        self.settings, self.db, self.github = settings, db, github
        self.explainer = explainer or Explainer(settings)

    @staticmethod
    def context(claim, **extra) -> dict:
        return {**claim.model_dump(), **extra}

    def analyze(self, claim) -> dict:
        with self.db.transaction("run:" + claim.run_key) as conn:
            run = self.db.leased_run(conn, claim)
            if run["report"]:
                return self.context(claim, report=json.loads(run["report"]))
            actual = self.github.get_run(run)
            expected = {
                "id": run["run_id"],
                "run_attempt": run["attempt"],
                "workflow_id": run["workflow_id"],
                "run_number": run["run_number"],
                "head_branch": run["branch"],
                "head_sha": run["head_sha"],
                "conclusion": run["conclusion"],
                "status": "completed",
            }
            if any(actual.get(key) != value for key, value in expected.items()):
                raise UpstreamError(
                    "Webhook metadata does not match the requested GitHub run attempt"
                )
            findings, jobs = [], self.github.jobs(run)
            failed = [job for job in jobs if job.get("conclusion") in ("failure", "timed_out")]
            if len(failed) > self.settings.max_failed_jobs:
                raise UpstreamError(
                    "Failed job limit exceeded; increase MAX_FAILED_JOBS deliberately"
                )
            if run["conclusion"] != "success" and not failed:
                failed = [{"id": None, "name": "Workflow", "steps": []}]
            for job in failed:
                if job["id"]:
                    log, truncated, available = self.github.log(run["repo"], job["id"])
                else:
                    log, truncated, available = "No failed job log was available.", False, False
                step = next(
                    (
                        step["name"]
                        for step in job.get("steps", [])
                        if step.get("conclusion") in ("failure", "timed_out")
                    ),
                    "Unknown step",
                )
                finding = analyze_log(
                    repo=run["repo"],
                    workflow_id=run["workflow_id"],
                    branch=run["branch"],
                    job=job["name"],
                    step=step,
                    log=log,
                    secrets=self.settings.secret_values,
                    truncated=truncated,
                    available=available,
                ).model_dump()
                finding["markdown"] = markdown_report(run, finding)
                findings.append(finding)
            report = {
                "conclusion": run["conclusion"],
                "repo": run["repo"],
                "run_url": run["run_url"],
                "findings": findings,
            }
            self.db.update(conn, run["key"], report=json.dumps(report), last_error=None)
            return self.context(claim, report=report)

    def enrich(self, claim) -> dict:
        with self.db.transaction("run:" + claim.run_key) as conn:
            run = self.db.leased_run(conn, claim)
            if not run["report"]:
                raise Conflict("Analyze the run before explanation")
            report = json.loads(run["report"])
            # Published reports are immutable. Old workflows can finish during an upgrade.
            if "llm" in report or run["published"]:
                return self.context(claim, report=report)
            report["llm"] = self.explainer.explain(report)
            if report["llm"]["status"] == "completed":
                for insight in report["llm"]["findings"]:
                    report["findings"][insight["finding_index"]]["markdown"] += (
                        explanation_markdown(insight)
                    )
            self.db.update(conn, run["key"], report=json.dumps(report), last_error=None)
            return self.context(claim, report=report)

    def publish(self, claim) -> dict:
        # Serialize publication across workers, including external calls. At portfolio/MVP
        # scale this is a deliberate tradeoff for incident-level idempotency.
        with self.db.transaction("publication") as conn:
            run = self.db.leased_run(conn, claim)
            if not run["report"]:
                raise Conflict("Analyze the run before publication")
            report = json.loads(run["report"])
            if run["published"]:
                return self.context(claim, report=report)
            actions = []
            if run["conclusion"] == "success":
                actions = self._recover(conn, run)
            else:
                for finding in report["findings"]:
                    actions.append(self._failure(conn, run, finding))
            report["publication"] = {"mode": self.settings.publish_mode, "actions": actions}
            self.db.update(
                conn, run["key"], published=True, report=json.dumps(report), last_error=None
            )
            return self.context(claim, report=report)

    def _failure(self, conn, run: dict, finding: dict) -> dict:
        fingerprint = finding["fingerprint"]
        effect_key = f"{run['key']}:{fingerprint}:failure"
        saved = self.db.effect(conn, effect_key)
        if saved:
            return saved
        previous = (
            conn.execute(sa.select(incidents).where(incidents.c.fingerprint == fingerprint))
            .mappings()
            .first()
        )
        if previous and (run["run_number"], run["attempt"]) < (
            previous["run_number"],
            previous["attempt"],
        ):
            result = {"action": "ignored_stale_failure", "fingerprint": fingerprint}
            self.db.save_effect(conn, effect_key, result)
            return result
        number, url = (
            (previous["issue_number"], previous["issue_url"]) if previous else (None, None)
        )
        action = "update" if previous else "create"
        if self.settings.publish_mode == "github":
            issue_marker = f"<!-- ci-triage:{fingerprint} -->"
            occurrence_marker = (
                f"<!-- ci-triage-run:{run['run_id']}:{run['attempt']}:{fingerprint} -->"
            )
            body = f"{finding['markdown']}\n{issue_marker}\n{occurrence_marker}"
            if number is None:
                issue = self.github.find_issue(run["repo"], issue_marker)
                if issue is None:
                    title = f"[CI] {finding['category']}: {finding['job']}"[:200]
                    issue = self.github.create_issue(run["repo"], title, body)
                number, url = issue["number"], issue["html_url"]
            issue = self.github.get_issue(run["repo"], number)
            if issue["state"] == "closed":
                self.github.set_issue_state(run["repo"], number, "open")
            self.github.comment_once(run["repo"], number, body, occurrence_marker)
        values = dict(
            repo=run["repo"],
            workflow_id=run["workflow_id"],
            branch=run["branch"],
            job=finding["job"],
            issue_number=number,
            issue_url=url,
            state="open",
            run_number=run["run_number"],
            attempt=run["attempt"],
            occurrences=(previous["occurrences"] if previous else 0) + 1,
        )
        if previous:
            conn.execute(
                incidents.update().where(incidents.c.fingerprint == fingerprint).values(**values)
            )
        else:
            conn.execute(incidents.insert().values(fingerprint=fingerprint, **values))
        result = {"action": action, "fingerprint": fingerprint, "issue_url": url}
        self.db.save_effect(conn, effect_key, result)
        return result

    def _recover(self, conn, run: dict) -> list[dict]:
        candidates = (
            conn.execute(
                sa.select(incidents).where(
                    incidents.c.repo == run["repo"],
                    incidents.c.workflow_id == run["workflow_id"],
                    incidents.c.branch == run["branch"],
                    incidents.c.state == "open",
                )
            )
            .mappings()
            .all()
        )
        actions = []
        for incident in candidates:
            if (run["run_number"], run["attempt"]) <= (incident["run_number"], incident["attempt"]):
                continue
            fingerprint = incident["fingerprint"]
            if self.settings.publish_mode == "github" and incident["issue_number"]:
                marker = (
                    f"<!-- ci-triage-recovery:{run['run_id']}:{run['attempt']}:{fingerprint} -->"
                )
                self.github.comment_once(
                    run["repo"],
                    incident["issue_number"],
                    "CI recovered on this workflow and branch.\n\n"
                    f"[Successful run]({run['run_url']})\n{marker}",
                    marker,
                )
                if self.settings.auto_close_on_success:
                    self.github.set_issue_state(run["repo"], incident["issue_number"], "closed")
            conn.execute(
                incidents.update()
                .where(incidents.c.fingerprint == fingerprint)
                .values(state="recovered", run_number=run["run_number"], attempt=run["attempt"])
            )
            actions.append(
                {
                    "action": "recovered",
                    "fingerprint": fingerprint,
                    "issue_url": incident["issue_url"],
                }
            )
        return actions

    def notify(self, claim) -> dict:
        with self.db.transaction("run:" + claim.run_key) as conn:
            run = self.db.leased_run(conn, claim)
            if not run["published"]:
                raise Conflict("Publish the report before notification")
            report = json.loads(run["report"])
            if run["notified"]:
                return self.context(claim, report=report)
            lines = [
                f"CI {run['conclusion']}: {run['repo']}",
                f"Workflow: {run['workflow_name']}",
                f"Branch: {run['branch']}",
            ]
            llm = report.get("llm", {})
            if llm.get("status") == "completed":
                lines.extend(["\nAI summary (hypothesis; review required):", llm["summary"]])
                for insight in llm["findings"][:1]:
                    lines.extend("Check: " + value for value in insight["next_steps"][:2])
            for finding in report["findings"]:
                lines.extend(
                    [
                        f"\n{finding['job']} / {finding['step']}",
                        f"Category: {finding['category']}",
                        finding["evidence"],
                    ]
                )
            links = [run["run_url"]]
            for action in report["publication"]["actions"]:
                if action.get("issue_url") and action["issue_url"] not in links and len(links) < 6:
                    links.append(action["issue_url"])
            suffix = "\n\n" + "\n".join(links)
            text = "\n".join(lines)[: 3_800 - len(suffix)] + suffix
            enabled = bool(self.settings.telegram_bot_token.get_secret_value())
            if enabled:
                try:
                    response = httpx.post(
                        "https://api.telegram.org/bot"
                        + self.settings.telegram_bot_token.get_secret_value()
                        + "/sendMessage",
                        json={
                            "chat_id": self.settings.telegram_chat_id,
                            "text": text,
                            "link_preview_options": {"is_disabled": True},
                        },
                        timeout=self.settings.request_timeout_seconds,
                    )
                    if response.status_code != 200 or not response.json().get("ok"):
                        raise UpstreamError("Telegram rejected the notification")
                except (httpx.HTTPError, ValueError) as exc:
                    raise UpstreamError("Telegram notification failed") from exc
            report["telegram"] = {"mode": "sent" if enabled else "draft", "text": text}
            self.db.update(
                conn, run["key"], notified=True, report=json.dumps(report), last_error=None
            )
            return self.context(claim, report=report)

    def complete(self, claim) -> dict:
        with self.db.transaction("run:" + claim.run_key) as conn:
            from .db import runs

            row = (
                conn.execute(sa.select(runs).where(runs.c.key == claim.run_key)).mappings().first()
            )
            if row and row["status"] == "done" and row["lease_token"] == claim.lease_token:
                return {"status": "done", "run_key": claim.run_key}
            run = self.db.leased_run(conn, claim)
            if not run["published"] or not run["notified"]:
                raise Conflict("Publication and notification must finish before acknowledgement")
            self.db.update(conn, run["key"], status="done", lease_until=None, last_error=None)
            return {"status": "done", "run_key": claim.run_key}
