"""
Cron scheduler — runs jobs defined in config.yml using APScheduler.
Each job runs a shell command and logs output.
"""

import asyncio
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class JobStatus:
    def __init__(self, job_id: str, name: str, schedule: str, command: str,
                 agent_id: str = "", notify_channels: list = None, notify_users: list = None,
                 timeout: int = 600):
        self.id = job_id
        self.name = name
        self.schedule = schedule
        self.command = command
        self.agent_id = agent_id  # if set, run via agent backend instead of shell
        self.notify_channels = notify_channels or []
        self.notify_users = notify_users or []
        self.timeout = timeout
        self.last_run: datetime | None = None
        self.last_status: str = "never"
        self.last_output: str = ""
        self.last_error: str = ""
        self.next_run: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "schedule": self.schedule,
            "command": self.command,
            "agent_id": self.agent_id,
            "notify_channels": self.notify_channels,
            "notify_users": self.notify_users,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_status": self.last_status,
            "last_output": self.last_output[-4000:] if self.last_output else "",
            "last_error": self.last_error,
            "next_run": self.next_run.isoformat() if self.next_run else None,
        }


class Scheduler:
    def __init__(self, config_path: str | None = None):
        self._scheduler = AsyncIOScheduler()
        self._jobs: dict[str, JobStatus] = {}
        self._agents_manager = None
        self._agent_backend = None
        self._connexion_manager = None
        self._config_path = config_path

    def set_agent_backend(self, agents_manager: Any, agent_backend: Any):
        """Inject agent backend for agent-triggered cron jobs."""
        self._agents_manager = agents_manager
        self._agent_backend = agent_backend

    def set_connexion_manager(self, connexion_manager: Any):
        """Inject connexion manager for post-job notifications."""
        self._connexion_manager = connexion_manager

    def load_jobs(self, crons: list[dict]):
        """Load cron jobs from config and schedule them."""
        for cron in crons:
            if not cron.get("enabled", True):
                continue

            job_id = cron.get("id", cron.get("name", "unnamed"))
            name = cron.get("name", job_id)
            schedule = cron.get("schedule", "0 * * * *")
            command = cron.get("command", "echo 'no command'")
            agent_id = cron.get("agent_id", "")
            timeout = int(cron.get("timeout", 600))

            # Support notify: [channels] or notify: {channels: [...], user: "...", users: [...]}
            raw_notify = cron.get("notify", [])
            if isinstance(raw_notify, dict):
                notify_channels = raw_notify.get("channels", [])
                # Accept users list OR legacy single user
                if raw_notify.get("users"):
                    notify_users = raw_notify["users"]
                elif raw_notify.get("user"):
                    notify_users = [raw_notify["user"]]
                else:
                    notify_users = []
            elif isinstance(raw_notify, list):
                notify_channels = raw_notify
                notify_users = [cron["notify_user"]] if cron.get("notify_user") else []
            else:
                notify_channels = []
                notify_users = []

            status = JobStatus(job_id, name, schedule, command, agent_id, notify_channels, notify_users, timeout)
            self._jobs[job_id] = status

            self._scheduler.add_job(
                self._run_job,
                CronTrigger.from_crontab(schedule),
                args=[job_id],
                id=job_id,
                replace_existing=True,
            )
            mode = f"agent:{agent_id}" if agent_id else command
            logger.info(f"Scheduled job '{name}' [{schedule}]: {mode}")

    async def _run_job(self, job_id: str):
        status = self._jobs.get(job_id)
        if not status:
            return

        logger.info(f"Running cron job: {status.name}")
        status.last_run = datetime.now()

        try:
            # Agent-based job
            if status.agent_id and self._agents_manager and self._agent_backend:
                agent = self._agents_manager.get_agent(status.agent_id)
                if not agent:
                    raise RuntimeError(f"Agent not found: {status.agent_id}")
                task = status.command or "Execute your scheduled task."

                # Inject SKILL.md matching this cron's id, if it exists
                system_prompt = agent["system_prompt"]
                for _base in [Path("/skills"), Path("skills")]:
                    skill_path = _base / job_id / "SKILL.md"
                    if skill_path.exists():
                        break
                if skill_path.exists():
                    skill_content = skill_path.read_text(encoding="utf-8")
                    system_prompt = system_prompt + f"\n\n---\n# Skill instructions for this task:\n{skill_content}"
                    logger.info(f"Injected SKILL.md for cron '{job_id}'")

                output = await asyncio.wait_for(
                    self._agent_backend.run_agent(
                        system_prompt=system_prompt,
                        task=task,
                        model=agent.get("model"),
                    ),
                    timeout=status.timeout,
                )
                status.last_output = output
                status.last_status = "ok"
                status.last_error = ""
                logger.info(f"Agent job '{status.name}' finished OK")

            else:
                # Shell command job
                proc = await asyncio.create_subprocess_shell(
                    status.command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=status.timeout)
                output = stdout.decode() if stdout else ""
                status.last_output = output
                status.last_status = "ok" if proc.returncode == 0 else f"error:{proc.returncode}"
                if proc.returncode == 0:
                    status.last_error = ""
                logger.info(f"Job '{status.name}' finished: {status.last_status}")

        except asyncio.TimeoutError:
            status.last_status = "timeout"
            status.last_error = f"Job timed out after {status.timeout}s"
            logger.error(f"Job '{status.name}' timed out")
        except Exception as e:
            import traceback
            status.last_status = "error"
            status.last_error = traceback.format_exc()
            logger.error(f"Job '{status.name}' exception: {e}")

        # Update next run time
        job = self._scheduler.get_job(job_id)
        if job and job.next_run_time:
            status.next_run = job.next_run_time

        # Send notifications
        has_error = status.last_status in ("error", "timeout") or status.last_status.startswith("error:")
        if self._connexion_manager:
            try:
                from core import notifications
                import re as _re

                if has_error:
                    # Always notify on error — use the job's configured channels/users, or fall back to default
                    notify_channels = status.notify_channels or ["telegram"]
                    notify_users = status.notify_users or []
                    err_msg = status.last_error or status.last_status
                    subject = f"⚠️ Erreur cron : {status.name}"
                    body = f"Statut : {status.last_status}\n\nErreur :\n{err_msg[:2000]}"
                    if status.last_output:
                        body += f"\n\nDernier output :\n{status.last_output[:1000]}"
                    for uid in notify_users:
                        await notifications.notify(
                            self._connexion_manager, uid,
                            notify_channels, subject, body,
                        )
                elif status.notify_channels and status.notify_users:
                    body = (status.last_output[:100000] or "No output.").strip()
                    # If output contains HTML (e.g. morning-brief), extract just the HTML part
                    _html_match = _re.search(r'(<!DOCTYPE html>.*?</html>)', body, _re.DOTALL | _re.IGNORECASE)
                    if _html_match:
                        body = _html_match.group(1)
                    # Extract <title> for better email subject
                    _title_match = _re.search(r'<title>(.*?)</title>', body, _re.IGNORECASE)
                    if _title_match:
                        subject = _title_match.group(1).strip()
                    else:
                        subject = f"[Cron] {status.name} — {status.last_status}"
                    for uid in status.notify_users:
                        await notifications.notify(
                            self._connexion_manager, uid,
                            status.notify_channels, subject, body,
                        )
            except Exception as e:
                logger.error(f"Notification error for job '{status.name}': {e}")

    def start(self):
        self._scheduler.start()
        # Set initial next_run for all jobs
        for job_id, status in self._jobs.items():
            job = self._scheduler.get_job(job_id)
            if job and job.next_run_time:
                status.next_run = job.next_run_time

    def stop(self):
        self._scheduler.shutdown(wait=False)

    def get_status(self) -> list[dict]:
        return [s.to_dict() for s in self._jobs.values()]

    def set_job_notify(self, job_id: str, channels: list, users: list) -> bool:
        """Update notify config for a job at runtime."""
        if job_id not in self._jobs:
            return False
        self._jobs[job_id].notify_channels = channels
        self._jobs[job_id].notify_users = users if isinstance(users, list) else ([users] if users else [])
        return True

    async def run_job_now(self, job_id: str):
        """Manually trigger a job immediately."""
        if job_id not in self._jobs:
            raise ValueError(f"Job not found: {job_id}")
        await self._run_job(job_id)

    # ── Runtime job management (add/update/remove + persist) ────

    def _read_config(self) -> dict:
        """Read config.yml. Returns empty dict if not available."""
        if not self._config_path:
            return {}
        try:
            import yaml
            with open(self._config_path) as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Could not read config: {e}")
            return {}

    def _write_config_crons(self, crons: list):
        """Persist cron list to config.yml."""
        if not self._config_path:
            return
        try:
            import yaml
            cfg = self._read_config()
            cfg["crons"] = crons
            with open(self._config_path, "w") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            logger.error(f"Could not write config: {e}")

    def add_job_runtime(self, cron: dict):
        """Add a new cron job at runtime and persist to config.yml."""
        job_id = cron.get("id", cron.get("name", "unnamed"))
        name = cron.get("name", job_id)
        schedule = cron.get("schedule", "0 * * * *")
        command = cron.get("command", "echo 'no command'")
        agent_id = cron.get("agent_id", "")

        raw_notify = cron.get("notify", [])
        if isinstance(raw_notify, dict):
            notify_channels = raw_notify.get("channels", [])
            notify_users = raw_notify.get("users") or ([raw_notify["user"]] if raw_notify.get("user") else [])
        elif isinstance(raw_notify, list):
            notify_channels = raw_notify
            notify_users = [cron["notify_user"]] if cron.get("notify_user") else []
        else:
            notify_channels = []
            notify_users = []

        status = JobStatus(job_id, name, schedule, command, agent_id, notify_channels, notify_users)
        self._jobs[job_id] = status

        self._scheduler.add_job(
            self._run_job,
            CronTrigger.from_crontab(schedule),
            args=[job_id],
            id=job_id,
            replace_existing=True,
        )
        job = self._scheduler.get_job(job_id)
        if job and job.next_run_time:
            status.next_run = job.next_run_time

        logger.info(f"Runtime: added job '{name}' [{schedule}]")

        # Persist
        cfg = self._read_config()
        crons = cfg.get("crons", [])
        crons = [c for c in crons if c.get("id") != job_id]
        crons.append(cron)
        self._write_config_crons(crons)

    def update_job_runtime(self, cron_id: str, data: dict):
        """Update an existing cron job at runtime and persist to config.yml."""
        if cron_id not in self._jobs:
            raise ValueError(f"Job not found: {cron_id}")

        status = self._jobs[cron_id]
        if "name" in data:
            status.name = data["name"]
        if "command" in data:
            status.command = data["command"]
        if "agent_id" in data:
            status.agent_id = data["agent_id"]
        if "schedule" in data:
            status.schedule = data["schedule"]
            job = self._scheduler.get_job(cron_id)
            if job:
                job.reschedule(CronTrigger.from_crontab(data["schedule"]))
                status.next_run = job.next_run_time

        logger.info(f"Runtime: updated job '{cron_id}'")

        # Persist
        cfg = self._read_config()
        crons = cfg.get("crons", [])
        for c in crons:
            if c.get("id") == cron_id:
                c.update(data)
                break
        self._write_config_crons(crons)

    def remove_job_runtime(self, cron_id: str):
        """Remove a cron job at runtime and persist to config.yml."""
        if cron_id not in self._jobs:
            raise ValueError(f"Job not found: {cron_id}")

        try:
            self._scheduler.remove_job(cron_id)
        except Exception:
            pass
        del self._jobs[cron_id]

        logger.info(f"Runtime: removed job '{cron_id}'")

        # Persist
        cfg = self._read_config()
        crons = [c for c in cfg.get("crons", []) if c.get("id") != cron_id]
        self._write_config_crons(crons)
