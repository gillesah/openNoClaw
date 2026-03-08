"""
FastAPI web server — WebSocket chat, REST API, profile auth.
"""

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


# ── Profile helpers ───────────────────────────────────────────

def find_profile(profiles: list[dict], profile_id: str, password: str) -> dict | None:
    for p in profiles:
        if p.get("id") == profile_id:
            stored = p.get("password", "")
            if stored == "" or stored == password:
                return p
    return None


# ── App factory ───────────────────────────────────────────────

def create_app(
    chat_backend, agent_backend, memory, skills_manager, scheduler,
    usage_tracker, config: dict, auth_manager=None,
    agents_manager=None, context_manager=None, connexion_manager=None,
    gmail_manager=None, google_tasks_manager=None,
    config_path: str = "config.yml", user_memory=None,
) -> FastAPI:
    app = FastAPI(title="openNoClaw", version="0.3.0")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    profiles = config.get("profiles", [{"id": "default", "name": "User", "password": "", "admin": True}])

    # UserMemory — init here if not passed (main.py not mounted in Docker)
    if user_memory is None:
        from core.user_memory import UserMemory as _UM
        user_memory = _UM(data_dir=config.get("data_dir", "/data"))

    # ── Auth helper ───────────────────────────────────────────

    def parse_token(token: str) -> dict | None:
        if ":" not in token:
            profile_id, password = token, ""
        else:
            profile_id, _, password = token.partition(":")
        return find_profile(profiles, profile_id, password)

    # ── HTTP routes ───────────────────────────────────────────

    @app.get("/")
    async def root():
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/api/profiles")
    async def list_profiles():
        return [
            {"id": p["id"], "name": p.get("name", p["id"]), "has_password": bool(p.get("password", ""))}
            for p in profiles
        ]

    @app.get("/api/me")
    async def get_me(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        return {"id": p["id"], "name": p.get("name", p["id"]), "admin": p.get("admin", False), "can_manage": p.get("can_manage", False)}

    @app.get("/api/history/{user_id}")
    async def get_history(user_id: str, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if not p.get("admin") and p["id"] != user_id:
            raise HTTPException(status_code=403)
        return memory.get_history(user_id)

    @app.delete("/api/history/{user_id}")
    async def clear_history(user_id: str, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if not p.get("admin") and p["id"] != user_id:
            raise HTTPException(status_code=403)
        await memory.clear(user_id)
        return {"status": "cleared"}

    # Persistent cron notify overrides: /data/cron_notify.json
    _cron_notify_path = Path(config.get("data_dir", "/data")) / "cron_notify.json"

    def _load_cron_notify() -> dict:
        if _cron_notify_path.exists():
            try:
                return json.loads(_cron_notify_path.read_text())
            except Exception:
                pass
        return {}

    def _save_cron_notify(data: dict):
        _cron_notify_path.parent.mkdir(parents=True, exist_ok=True)
        _cron_notify_path.write_text(json.dumps(data, indent=2))

    # Apply persisted notify overrides on startup
    for job_id, cfg in _load_cron_notify().items():
        users = cfg.get("users") or ([cfg["user"]] if cfg.get("user") else [])
        scheduler.set_job_notify(job_id, cfg.get("channels", []), users)

    @app.get("/api/crons")
    async def get_crons(token: str = ""):
        if not parse_token(token):
            raise HTTPException(status_code=401)
        return scheduler.get_status()

    @app.put("/api/crons/{job_id}/notify")
    async def set_cron_notify(job_id: str, body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403, detail="Admin only")
        channels = body.get("channels", [])
        # Accept "users" (array) or legacy "user" (string)
        users = body.get("users") or ([body["user"]] if body.get("user") else [p["id"]])
        if not scheduler.set_job_notify(job_id, channels, users):
            raise HTTPException(status_code=404, detail="Job not found")
        overrides = _load_cron_notify()
        overrides[job_id] = {"channels": channels, "users": users}
        _save_cron_notify(overrides)
        return {"status": "ok", "channels": channels, "users": users}

    @app.post("/api/crons/{job_id}/run")
    async def run_cron_now(job_id: str, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403, detail="Admin only")
        asyncio.create_task(scheduler.run_job_now(job_id))
        return {"status": "triggered"}

    @app.get("/api/skills")
    async def get_skills(token: str = ""):
        if not parse_token(token):
            raise HTTPException(status_code=401)
        return {"skills": skills_manager.list_skills()}

    @app.post("/api/skills/reload")
    async def reload_skills(token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403, detail="Admin only")
        skills_manager.reload()
        return {"skills": skills_manager.list_skills()}

    @app.get("/api/usage")
    async def get_usage(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if p.get("admin"):
            return usage_tracker.get_all_stats()
        return {p["id"]: usage_tracker.get_stats(p["id"])}

    @app.get("/api/backend")
    async def get_backend(token: str = ""):
        if not parse_token(token):
            raise HTTPException(status_code=401)
        return {
            "chat": {"type": chat_backend.type, "model": chat_backend.model},
            "agents": {"type": agent_backend.type, "model": agent_backend.model},
            "is_api": chat_backend.type == "anthropic-api",
            "model": chat_backend.model,
        }

    # ── Admin: profiles ───────────────────────────────────────

    @app.get("/api/admin/profiles")
    async def admin_list_profiles(token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        return profiles

    @app.post("/api/admin/profiles")
    async def admin_create_profile(profile: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if not profile.get("id") or not profile.get("name"):
            raise HTTPException(status_code=400, detail="id and name required")
        if any(x["id"] == profile["id"] for x in profiles):
            raise HTTPException(status_code=409, detail="Profile ID already exists")
        new_profile = {
            "id": profile["id"],
            "name": profile["name"],
            "password": profile.get("password", ""),
            "admin": profile.get("admin", False),
        }
        profiles.append(new_profile)
        return new_profile

    @app.delete("/api/admin/profiles/{profile_id}")
    async def admin_delete_profile(profile_id: str, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        for i, prof in enumerate(profiles):
            if prof["id"] == profile_id:
                profiles.pop(i)
                return {"status": "deleted"}
        raise HTTPException(status_code=404)

    @app.put("/api/profiles/password")
    async def change_password(body: dict, token: str = ""):
        """Change own password. Admins can also change others' passwords."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        target_id = body.get("profile_id") or p["id"]
        # Non-admins can only change their own password
        if not p.get("admin") and target_id != p["id"]:
            raise HTTPException(status_code=403)
        current_pw = body.get("current_password", "")
        new_pw = body.get("new_password", "").strip()
        if not new_pw:
            raise HTTPException(status_code=400, detail="new_password required")

        # Find and validate target profile
        target = None
        for prof in profiles:
            if prof["id"] == target_id:
                target = prof
                break
        if target is None:
            raise HTTPException(status_code=404, detail="Profile not found")

        # Verify current password (admins changing another user's password skip this)
        if target_id == p["id"]:
            if target.get("password", "") != current_pw:
                raise HTTPException(status_code=403, detail="Wrong current password")

        # Update in-memory
        target["password"] = new_pw

        # Persist to config.yml
        try:
            import yaml
            cfg_path = Path(config_path)
            if cfg_path.exists():
                with open(cfg_path) as f:
                    cfg = yaml.safe_load(f) or {}
                for prof in cfg.get("profiles", []):
                    if prof.get("id") == target_id:
                        prof["password"] = new_pw
                        break
                with open(cfg_path, "w") as f:
                    yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            logger.error(f"Could not persist password change: {e}")

        return {"status": "ok"}

    # ── Admin: API key ────────────────────────────────────────

    _api_key_file = Path(config.get("data_dir", "/data")) / "api_key.txt"

    @app.get("/api/admin/apikey/status")
    async def apikey_status(token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        has_key = bool(
            _api_key_file.exists() and _api_key_file.read_text().strip()
        ) or bool(config.get("backend", {}).get("api_key", "").strip())
        return {"has_key": has_key, "backend_type": chat_backend.type, "model": chat_backend.model}

    @app.post("/api/admin/apikey/save")
    async def apikey_save(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        key = body.get("key", "").strip()
        if not key.startswith("sk-ant-"):
            raise HTTPException(status_code=400, detail="Invalid API key format")
        _api_key_file.parent.mkdir(parents=True, exist_ok=True)
        _api_key_file.write_text(key)
        if hasattr(chat_backend, "set_api_key"):
            chat_backend.set_api_key(key)
        return {"status": "ok"}

    # ── Admin: Claude Code CLI auth ───────────────────────────

    @app.get("/api/admin/auth/status")
    async def auth_status(token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if auth_manager is None:
            return {"authenticated": False, "detail": "Auth manager not available"}
        return {
            "authenticated": auth_manager.is_authenticated(),
            "agent_backend": agent_backend.type,
        }

    @app.post("/api/admin/auth/start")
    async def auth_start(token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if auth_manager is None:
            raise HTTPException(status_code=503, detail="Auth manager not available")
        result = await auth_manager.start_login()
        return result

    @app.post("/api/admin/auth/complete")
    async def auth_complete(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if auth_manager is None:
            raise HTTPException(status_code=503, detail="Auth manager not available")
        code = body.get("code", "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="code required")
        result = await auth_manager.complete_login(code)
        return result

    # ── Admin: Skills content ─────────────────────────────────

    @app.get("/api/admin/skills/{name}/content")
    async def get_skill_content(name: str, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        content = skills_manager.get_skill(name)
        if content is None:
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        return {"name": name, "content": content}

    @app.post("/api/admin/skills/{name}/update")
    async def update_skill_content(name: str, body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        content = body.get("content", "")
        skill_path = skills_manager.skills_dir / name / "SKILL.md"
        if not skill_path.exists():
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found on disk")
        skill_path.write_text(content, encoding="utf-8")
        skills_manager.reload()
        return {"status": "ok", "name": name}

    @app.get("/api/admin/skills/{name}/script")
    async def get_skill_script(name: str, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        content = skills_manager.get_skill_script(name)
        return {"name": name, "content": content or "", "exists": content is not None}

    @app.post("/api/admin/skills/{name}/script")
    async def update_skill_script(name: str, body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        try:
            skills_manager.update_skill_script(name, body.get("content", ""))
            return {"status": "ok"}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/admin/skills")
    async def create_skill(body: dict, token: str = ""):
        """Create a new skill directory with SKILL.md (and optionally skill.py)."""
        p = parse_token(token)
        if not p or not (p.get("admin") or p.get("can_manage")):
            raise HTTPException(status_code=403)
        name = body.get("name", "").strip().lower().replace(" ", "-")
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        skill_dir = skills_manager.skills_dir / name
        if skill_dir.exists():
            raise HTTPException(status_code=409, detail=f"Skill '{name}' already exists")
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(body.get("skill_md", f"# {name}\n\n"), encoding="utf-8")
        if body.get("skill_py"):
            (skill_dir / "skill.py").write_text(body["skill_py"], encoding="utf-8")
        skills_manager.reload()
        return {"status": "ok", "name": name}

    # ── Agents (shared across all users) ─────────────────────

    @app.get("/api/agents")
    async def list_agents(token: str = ""):
        if not parse_token(token):
            raise HTTPException(status_code=401)
        return agents_manager.list_agents() if agents_manager else []

    @app.post("/api/agents")
    async def create_agent(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not (p.get("admin") or p.get("can_manage")):
            raise HTTPException(status_code=403)
        if agents_manager is None:
            raise HTTPException(status_code=503)
        try:
            return agents_manager.create_agent(body)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @app.put("/api/agents/{agent_id}")
    async def update_agent(agent_id: str, body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if agents_manager is None:
            raise HTTPException(status_code=503)
        try:
            return agents_manager.update_agent(agent_id, body)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.delete("/api/agents/{agent_id}")
    async def delete_agent(agent_id: str, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if agents_manager is None:
            raise HTTPException(status_code=503)
        try:
            agents_manager.delete_agent(agent_id)
            return {"status": "deleted"}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ── Contexts (private per user) ───────────────────────────

    @app.get("/api/contexts")
    async def get_contexts(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if context_manager is None:
            return {"global": "", "projects": []}
        return context_manager.get_all(p["id"])

    @app.put("/api/contexts/global")
    async def update_global_context(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if context_manager is None:
            raise HTTPException(status_code=503)
        context_manager.set_global(p["id"], body.get("content", ""))
        return {"status": "ok"}

    @app.post("/api/contexts/projects")
    async def create_context_project(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if context_manager is None:
            raise HTTPException(status_code=503)
        return context_manager.create_project(p["id"], body)

    @app.put("/api/contexts/projects/{project_id}")
    async def update_context_project(project_id: str, body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if context_manager is None:
            raise HTTPException(status_code=503)
        try:
            return context_manager.update_project(p["id"], project_id, body)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.delete("/api/contexts/projects/{project_id}")
    async def delete_context_project(project_id: str, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if context_manager is None:
            raise HTTPException(status_code=503)
        try:
            context_manager.delete_project(p["id"], project_id)
            return {"status": "deleted"}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ── Connexions (private per user) ────────────────────────

    @app.get("/api/connexions")
    async def get_connexions(token: str = "", for_user: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            return {"telegram": {}, "email": {}, "mcps": []}
        target = for_user if for_user and p.get("admin") else p["id"]
        return connexion_manager.get_all(target)

    @app.get("/api/profiles")
    async def get_profiles(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        profiles = config.get("profiles", [])
        return [{"id": pr["id"], "name": pr.get("name", pr["id"])} for pr in profiles]

    @app.put("/api/connexions/telegram")
    async def update_telegram_conn(body: dict, token: str = "", for_user: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        target = for_user if for_user and p.get("admin") else p["id"]
        connexion_manager.update_telegram(target, body)
        return {"status": "ok"}

    @app.put("/api/connexions/email")
    async def update_email_conn(body: dict, token: str = "", for_user: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        target = for_user if for_user and p.get("admin") else p["id"]
        connexion_manager.update_email(target, body)
        return {"status": "ok"}

    @app.put("/api/connexions/social")
    async def update_social_conn(body: dict, token: str = "", for_user: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        target = for_user if for_user and p.get("admin") else p["id"]
        connexion_manager.update_social(target, body)
        return {"status": "ok"}

    @app.put("/api/connexions/leclerc")
    async def update_leclerc_conn(body: dict, token: str = "", for_user: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        target = for_user if for_user and p.get("admin") else p["id"]
        connexion_manager.update_leclerc(target, body)
        return {"status": "ok"}

    @app.put("/api/connexions/notion")
    async def update_notion_conn(body: dict, token: str = "", for_user: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        target = for_user if for_user and p.get("admin") else p["id"]
        connexion_manager.update_notion(target, body)
        return {"status": "ok"}

    @app.get("/api/connexions/mcps")
    async def get_mcps(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            return []
        return connexion_manager.get_mcps(p["id"])

    @app.post("/api/connexions/mcps")
    async def add_mcp(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        return connexion_manager.add_mcp(p["id"], body)

    @app.put("/api/connexions/mcps/{mcp_id}")
    async def update_mcp(mcp_id: str, body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        try:
            return connexion_manager.update_mcp(p["id"], mcp_id, body)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.delete("/api/connexions/mcps/{mcp_id}")
    async def delete_mcp(mcp_id: str, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        try:
            connexion_manager.delete_mcp(p["id"], mcp_id)
            return {"status": "deleted"}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/connexions/catalog")
    async def get_mcp_catalog(token: str = ""):
        if not parse_token(token):
            raise HTTPException(status_code=401)
        from core.connexion_manager import ConnexionManager as CM
        return CM.get_catalog()

    @app.post("/api/connexions/test/telegram")
    async def test_telegram(token: str = "", for_user: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        target = for_user if for_user and p.get("admin") else p["id"]
        from core.notifications import send_telegram
        tg = connexion_manager.get_telegram(target)
        if not tg.get("bot_token") or not tg.get("chat_id"):
            raise HTTPException(status_code=400, detail="Telegram not configured")
        ok = await send_telegram(tg["bot_token"], tg["chat_id"], "✅ openNoClaw — Test message OK!")
        return {"ok": ok}

    @app.post("/api/connexions/test/email")
    async def test_email(body: dict, token: str = "", for_user: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        target = for_user if for_user and p.get("admin") else p["id"]
        from core.notifications import send_email
        email_cfg = connexion_manager.get_email(target)
        if not email_cfg.get("smtp_host") or not email_cfg.get("smtp_user"):
            raise HTTPException(status_code=400, detail="Email SMTP not configured")
        to = body.get("to") or email_cfg.get("from_email") or email_cfg.get("smtp_user")
        ok = await send_email(email_cfg, to, "openNoClaw — Test email", "This is a test email from openNoClaw. It works!")
        return {"ok": ok}

    # ── Bot settings ─────────────────────────────────────────

    _bot_file = Path(config.get("data_dir", "/data")) / "bot_settings.json"
    _BOT_DEFAULTS = {"name": "openNoClaw", "avatar": "bot-nexus"}

    def _load_bot():
        if _bot_file.exists():
            try:
                return {**_BOT_DEFAULTS, **json.loads(_bot_file.read_text())}
            except Exception:
                pass
        return dict(_BOT_DEFAULTS)

    @app.get("/api/bot")
    async def get_bot():
        return _load_bot()

    @app.put("/api/bot")
    async def update_bot(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        settings = {**_BOT_DEFAULTS}
        if body.get("name", "").strip():
            settings["name"] = body["name"].strip()
        if body.get("avatar", "").strip():
            settings["avatar"] = body["avatar"].strip()
        _bot_file.parent.mkdir(parents=True, exist_ok=True)
        _bot_file.write_text(json.dumps(settings, indent=2))
        return settings

    # ── GitHub connexion ─────────────────────────────────────

    @app.put("/api/connexions/github")
    async def update_github_conn(body: dict, token: str = "", for_user: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        target = for_user if for_user and p.get("admin") else p["id"]
        connexion_manager.update_github(target, body)
        return {"status": "ok"}

    @app.put("/api/connexions/linear")
    async def update_linear_conn(body: dict, token: str = "", for_user: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        target = for_user if for_user and p.get("admin") else p["id"]
        connexion_manager.update_linear(target, body)
        return {"status": "ok"}

    # ── Actions (run-action blocks) ───────────────────────────

    @app.post("/api/actions/github-merge")
    async def action_github_merge(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        gh = connexion_manager.get_github(p["id"])
        if not gh.get("token"):
            raise HTTPException(status_code=400, detail="GitHub token not configured in Connexions")
        repo_owner = body.get("repo_owner") or gh.get("repo_owner", "")
        repo_name = body.get("repo_name") or gh.get("repo_name", "")
        base = body.get("base", "main")
        head = body.get("head", "preprod")
        if not repo_owner or not repo_name:
            raise HTTPException(status_code=400, detail="repo_owner and repo_name required")
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}/merges",
                headers={
                    "Authorization": f"token {gh['token']}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json={"base": base, "head": head,
                      "commit_message": f"chore: merge {head} → {base} [openNoClaw]"},
            )
        if resp.status_code == 201:
            sha = resp.json().get("sha", "")[:7]
            return {"ok": True, "message": f"Merged {head} → {base} ({sha})"}
        elif resp.status_code == 204:
            return {"ok": True, "message": f"{head} already up to date with {base}"}
        else:
            msg = resp.json().get("message", "Unknown error") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            return {"ok": False, "message": f"GitHub merge failed: {msg} (HTTP {resp.status_code})"}

    @app.post("/api/actions/linear-status")
    async def action_linear_status(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        lin = connexion_manager.get_linear(p["id"])
        api_key = lin.get("api_key") or ""
        team_id = body.get("team_id", "fbdb8878-1698-4c60-861d-4132caf3d7c2")
        if not api_key:
            raise HTTPException(status_code=400, detail="Linear API key not configured in Connexions")
        import httpx as _httpx
        query = """{ team(id: "%s") { issues(filter: { state: { type: { nin: ["completed", "cancelled"] } } }) {
          nodes { identifier title state { name } } } } }""" % team_id
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://api.linear.app/graphql",
                                     json={"query": query},
                                     headers={"Authorization": api_key, "Content-Type": "application/json"})
        resp.raise_for_status()
        issues = resp.json()["data"]["team"]["issues"]["nodes"]
        by_state: dict = {}
        for i in issues:
            s = i["state"]["name"]
            by_state.setdefault(s, []).append(f"[{i['identifier']}] {i['title']}")
        lines = []
        for state, items in sorted(by_state.items()):
            lines.append(f"**{state}** ({len(items)})")
            for item in items:
                lines.append(f"  • {item}")
        return {"ok": True, "message": "\n".join(lines) or "No active issues"}

    @app.post("/api/actions/linear-prod")
    async def action_linear_prod(body: dict, token: str = ""):
        """Check Linear board and merge preprod→main if ready."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503)
        lin = connexion_manager.get_linear(p["id"])
        gh = connexion_manager.get_github(p["id"])
        api_key = lin.get("api_key") or ""
        if not api_key:
            raise HTTPException(status_code=400, detail="Linear API key not configured")
        if not gh.get("token"):
            raise HTTPException(status_code=400, detail="GitHub token not configured")

        team_id = body.get("team_id", "fbdb8878-1698-4c60-861d-4132caf3d7c2")
        to_check_id = "8c374e33-9074-489d-9069-46f61265566f"
        to_prod_id  = "3d5140b7-a4f8-4ce6-a365-ebf70e0a8fa6"
        done_id     = "66d4aeaf-02e5-4670-ab71-5bc0b0a9ee50"

        import httpx as _httpx
        # Filter by specific state IDs — "To Prod" has type "completed" so we can't use type filter
        query = ('{ team(id: "%s") { issues(filter: { state: { id: { in: ["%s", "%s"] } } }, first: 50) {'
                 ' nodes { id identifier title state { id name } } } } }') % (team_id, to_check_id, to_prod_id)
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://api.linear.app/graphql",
                                     json={"query": query},
                                     headers={"Authorization": api_key, "Content-Type": "application/json"})
        resp.raise_for_status()
        issues = resp.json()["data"]["team"]["issues"]["nodes"]

        to_check = [i for i in issues if i["state"]["id"] == to_check_id]
        to_prod   = [i for i in issues if i["state"]["id"] == to_prod_id]

        if to_check:
            blocked = "\n".join(f"  • [{i['identifier']}] {i['title']}" for i in to_check)
            return {"ok": False, "message": f"Blocked — {len(to_check)} ticket(s) still in To Check:\n{blocked}"}

        if not to_prod:
            return {"ok": False, "message": "No tickets in To Prod. Nothing to deploy."}

        # Merge preprod → main via GitHub
        repo_owner = body.get("repo_owner") or gh.get("repo_owner", "")
        repo_name  = body.get("repo_name")  or gh.get("repo_name", "")
        if not repo_owner or not repo_name:
            return {"ok": False, "message": "repo_owner and repo_name not configured in GitHub connexion"}

        async with _httpx.AsyncClient(timeout=15) as client:
            merge_resp = await client.post(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}/merges",
                headers={"Authorization": f"token {gh['token']}", "Accept": "application/vnd.github.v3+json"},
                json={"base": "main", "head": "preprod",
                      "commit_message": "chore: merge preprod → main [openNoClaw deploy]"},
            )
        if merge_resp.status_code not in (201, 204):
            msg = merge_resp.json().get("message", merge_resp.text)
            return {"ok": False, "message": f"GitHub merge failed: {msg}"}

        sha = merge_resp.json().get("sha", "")[:7] if merge_resp.status_code == 201 else "already-merged"

        # Move To Prod tickets → Done
        move_mut = """mutation { issueUpdate(id: "%s", input: {stateId: "%s"}) { success } }"""
        moved = []
        async with _httpx.AsyncClient(timeout=15) as client:
            for issue in to_prod:
                r = await client.post("https://api.linear.app/graphql",
                                      json={"query": move_mut % (issue["id"], done_id)},
                                      headers={"Authorization": api_key, "Content-Type": "application/json"})
                if r.status_code == 200:
                    moved.append(f"[{issue['identifier']}] {issue['title']}")

        done_list = "\n".join(f"  ✓ {t}" for t in moved)
        return {
            "ok": True,
            "message": f"Deployed! Merged preprod → main ({sha})\n\nTickets → Done:\n{done_list}"
        }

    # ── Gmail OAuth2 (private per user) ──────────────────────

    # Temporary store for pending Gmail auth (user_id → redirect_uri)
    _gmail_pending: dict = {}

    @app.get("/api/gmail/status")
    async def gmail_status(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None:
            return {"connected": False, "client_id": "", "has_secret": False, "email": ""}
        return gmail_manager.get_status(p["id"])

    @app.put("/api/gmail/credentials")
    async def gmail_save_credentials(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None:
            raise HTTPException(status_code=503)
        gmail_manager.update_credentials(
            p["id"],
            body.get("client_id", ""),
            body.get("client_secret", ""),
        )
        return {"status": "ok"}

    @app.post("/api/gmail/auth/start")
    async def gmail_auth_start(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None:
            raise HTTPException(status_code=503)
        redirect_uri = body.get("redirect_uri", "")
        if not redirect_uri:
            raise HTTPException(status_code=400, detail="redirect_uri required")
        _gmail_pending[p["id"]] = redirect_uri
        try:
            url = gmail_manager.get_auth_url(p["id"], redirect_uri)
            return {"url": url}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/gmail/callback")
    async def gmail_callback(code: str = "", state: str = "", error: str = ""):
        from fastapi.responses import HTMLResponse
        if error:
            return HTMLResponse(f"<html><body><p>Auth error: {error}</p><script>window.close()</script></body></html>")
        if not code or not state:
            return HTMLResponse("<html><body><p>Missing code or state.</p></body></html>")
        user_id = state
        redirect_uri = _gmail_pending.get(user_id, "")
        if not redirect_uri:
            return HTMLResponse("<html><body><p>No pending auth for this user.</p></body></html>")
        if gmail_manager is None:
            return HTMLResponse("<html><body><p>Gmail manager not available.</p></body></html>")
        ok = await gmail_manager.exchange_code(user_id, code, redirect_uri)
        _gmail_pending.pop(user_id, None)
        if ok:
            return HTMLResponse("""<html><body style="font-family:sans-serif;background:#1a1a2e;color:#a0a0c0;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center"><p style="font-size:1.4rem;color:#7b6ef6">✓ Gmail connected!</p><p>You can close this tab.</p><script>setTimeout(()=>window.close(),2000)</script></div></body></html>""")
        return HTMLResponse("<html><body><p>Auth failed — check server logs.</p></body></html>")

    @app.get("/api/gmail/messages")
    async def gmail_list_messages(token: str = "", query: str = "in:inbox is:unread", max: int = 25):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None or not gmail_manager.is_connected(p["id"]):
            raise HTTPException(status_code=400, detail="Gmail not connected")
        max = min(max, 100)  # hard cap
        msgs = await gmail_manager.list_messages(p["id"], query=query, max_results=max)
        summaries = []
        for m in msgs:
            try:
                summaries.append(await gmail_manager.get_message_summary(p["id"], m["id"]))
            except Exception:
                pass
        return {"messages": summaries, "count": len(summaries)}

    @app.get("/api/gmail/messages/{msg_id}")
    async def gmail_get_message(msg_id: str, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None or not gmail_manager.is_connected(p["id"]):
            raise HTTPException(status_code=400, detail="Gmail not connected")
        return await gmail_manager.get_message_summary(p["id"], msg_id)

    @app.post("/api/gmail/messages/{msg_id}/archive")
    async def gmail_archive_message(msg_id: str, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None or not gmail_manager.is_connected(p["id"]):
            raise HTTPException(status_code=400, detail="Gmail not connected")
        await gmail_manager.archive_message(p["id"], msg_id)
        return {"ok": True}

    @app.delete("/api/gmail/disconnect")
    async def gmail_disconnect(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None:
            raise HTTPException(status_code=503)
        gmail_manager.disconnect(p["id"])
        return {"status": "ok"}

    # ── Google Tasks OAuth2 (global / admin) ─────────────────

    # Redirect URI used for the callback
    _GTASKS_REDIRECT_URI = "https://app.opennoclaw.com/api/google-tasks/callback"

    @app.get("/api/google-tasks/status")
    async def gtasks_status(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if google_tasks_manager is None:
            return {"connected": False, "client_id": "", "has_secret": False}
        return google_tasks_manager.get_status()

    @app.put("/api/google-tasks/credentials")
    async def gtasks_save_credentials(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if google_tasks_manager is None:
            raise HTTPException(status_code=503)
        google_tasks_manager.update_credentials(
            body.get("client_id", ""),
            body.get("client_secret", ""),
        )
        return {"status": "ok"}

    @app.post("/api/google-tasks/auth/start")
    async def gtasks_auth_start(token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if google_tasks_manager is None:
            raise HTTPException(status_code=503)
        # If Google Tasks has no credentials, borrow them from Gmail (same OAuth client)
        st = google_tasks_manager.get_status()
        if not st.get("client_id") and gmail_manager is not None:
            gm_st = gmail_manager.get_status(p["id"])
            if gm_st.get("client_id"):
                gmail_data = gmail_manager._load(p["id"])
                google_tasks_manager.update_credentials(
                    gmail_data.get("client_id", ""),
                    gmail_data.get("client_secret", ""),
                )
        try:
            url = google_tasks_manager.get_auth_url(_GTASKS_REDIRECT_URI)
            return {"url": url}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/google-tasks/callback")
    async def gtasks_callback(code: str = "", state: str = "", error: str = ""):
        from fastapi.responses import HTMLResponse
        if error:
            return HTMLResponse(f"<html><body><p>Auth error: {error}</p><script>window.close()</script></body></html>")
        if not code:
            return HTMLResponse("<html><body><p>Missing code.</p></body></html>")
        if google_tasks_manager is None:
            return HTMLResponse("<html><body><p>Google Tasks manager not available.</p></body></html>")
        ok = await google_tasks_manager.exchange_code(code, _GTASKS_REDIRECT_URI)
        if ok:
            return HTMLResponse("""<html><body style="font-family:sans-serif;background:#1a1a2e;color:#a0a0c0;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center"><p style="font-size:1.4rem;color:#7b6ef6">✓ Google Tasks connected!</p><p>Token saved and synced to Gulliver.</p><p>You can close this tab.</p><script>setTimeout(()=>window.close(),2000)</script></div></body></html>""")
        return HTMLResponse("<html><body><p>Auth failed — check server logs.</p></body></html>")

    @app.delete("/api/google-tasks/disconnect")
    async def gtasks_disconnect(token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if google_tasks_manager is None:
            raise HTTPException(status_code=503)
        google_tasks_manager.disconnect()
        return {"status": "ok"}

    # ── Notify (send-notification block) ─────────────────────

    @app.post("/api/connexions/notify")
    async def connexions_notify(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if connexion_manager is None:
            raise HTTPException(status_code=503, detail="Connexion manager not available")
        channel = body.get("channel", "")
        message = body.get("message", "")
        if not channel or not message:
            raise HTTPException(status_code=400, detail="channel and message required")
        from core.notifications import send_telegram, send_email
        if channel == "telegram":
            tg = connexion_manager.get_telegram(p["id"])
            if not tg.get("bot_token") or not tg.get("chat_id"):
                raise HTTPException(status_code=400, detail="Telegram not configured")
            ok = await send_telegram(tg["bot_token"], tg["chat_id"], message)
            return {"ok": ok}
        elif channel == "email":
            to = body.get("to", "")
            subject = body.get("subject", "openNoClaw notification")
            from core.notifications import send_email_smart
            result = await send_email_smart(gmail_manager, connexion_manager, p["id"],
                                            to or None, subject, message)
            if not result["ok"] and "No email" in result.get("message", ""):
                raise HTTPException(status_code=400, detail=result["message"])
            return {"ok": result["ok"], "method": result.get("method")}
        else:
            raise HTTPException(status_code=400, detail=f"Unknown channel: {channel}")

    # ── User Memory ───────────────────────────────────────────────

    @app.get("/api/memory")
    async def get_memory(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if user_memory is None:
            return {"content": ""}
        return {"content": user_memory.get(p["id"])}

    @app.put("/api/memory")
    async def update_memory(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if user_memory is None:
            raise HTTPException(status_code=503)
        user_memory.update(p["id"], body.get("content", ""))
        return {"status": "ok"}

    @app.post("/api/actions/remember")
    async def action_remember(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if user_memory is None:
            raise HTTPException(status_code=503)
        fact = body.get("fact", "").strip()
        if not fact:
            return {"ok": False, "message": "fact is required"}
        user_memory.add_fact(p["id"], fact)
        return {"ok": True, "message": f"Mémorisé : {fact}"}

    @app.post("/api/actions/forget")
    async def action_forget(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if user_memory is None:
            raise HTTPException(status_code=503)
        keyword = body.get("keyword", "").strip()
        if not keyword:
            return {"ok": False, "message": "keyword is required"}
        removed, _ = user_memory.forget(p["id"], keyword)
        if removed:
            return {"ok": True, "message": f"{removed} entrée(s) oubliée(s) contenant « {keyword} »"}
        return {"ok": False, "message": f"Aucune entrée trouvée avec « {keyword} »"}

    # ── Sessions ─────────────────────────────────────────────────

    @app.get("/api/sessions/{user_id}")
    async def list_sessions(user_id: str, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if not p.get("admin") and p["id"] != user_id:
            raise HTTPException(status_code=403)
        return {
            "active_session": memory.get_active_session_id(user_id),
            "sessions": memory.list_sessions(user_id),
        }

    @app.post("/api/sessions/{user_id}")
    async def create_session(user_id: str, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if not p.get("admin") and p["id"] != user_id:
            raise HTTPException(status_code=403)
        session_id = await memory.create_session(user_id)
        return {"session_id": session_id}

    @app.put("/api/sessions/{user_id}/{session_id}/activate")
    async def activate_session(user_id: str, session_id: str, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if not p.get("admin") and p["id"] != user_id:
            raise HTTPException(status_code=403)
        ok = await memory.switch_session(user_id, session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "ok"}

    @app.delete("/api/sessions/{user_id}/{session_id}")
    async def delete_session(user_id: str, session_id: str, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if not p.get("admin") and p["id"] != user_id:
            raise HTTPException(status_code=403)
        ok = await memory.delete_session(user_id, session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "deleted"}

    # ── Browser ───────────────────────────────────────────────

    @app.get("/api/browser/status")
    async def browser_status(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager
        available = await browser_manager.is_available()
        has_session = await browser_manager.has_session(p["id"])
        url = ""
        if has_session:
            session = await browser_manager.get_session(p["id"])
            url = await session.current_url()
        return {"available": available, "has_session": has_session, "url": url}

    @app.post("/api/browser/navigate")
    async def browser_navigate(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        url = body.get("url", "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="url required")
        from core.browser import browser_manager
        session = await browser_manager.get_session(p["id"])
        return await session.navigate(url)

    @app.post("/api/browser/action")
    async def browser_action(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        action = body.get("action", "")
        from core.browser import browser_manager
        if not await browser_manager.has_session(p["id"]):
            raise HTTPException(status_code=400, detail="No active browser session")
        session = await browser_manager.get_session(p["id"])
        if action == "click":
            return await session.click(int(body.get("x", 0)), int(body.get("y", 0)))
        elif action == "type":
            return await session.type_text(body.get("text", ""))
        elif action == "key":
            return await session.press_key(body.get("key", "Enter"))
        elif action == "scroll":
            return await session.scroll(int(body.get("delta_y", 300)))
        elif action == "back":
            return await session.go_back()
        elif action == "refresh":
            return await session.refresh()
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    @app.get("/api/browser/screenshot")
    async def browser_screenshot(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager
        if not await browser_manager.has_session(p["id"]):
            raise HTTPException(status_code=404, detail="No active session")
        session = await browser_manager.get_session(p["id"])
        img = await session.screenshot_b64()
        return {"screenshot": img, "url": await session.current_url()}

    @app.get("/api/browser/cookies")
    async def browser_cookies(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager
        if not await browser_manager.has_session(p["id"]):
            raise HTTPException(status_code=404, detail="No active session")
        session = await browser_manager.get_session(p["id"])
        cookies = await session.get_cookies()
        return {"cookies": cookies, "count": len(cookies)}

    @app.delete("/api/browser/close")
    async def browser_close(token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager
        await browser_manager.close_session(p["id"])
        return {"status": "closed"}

    # ── Browser actions (run-action blocks from chat) ─────────

    @app.post("/api/actions/browser-screenshot")
    async def action_browser_screenshot(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager as bm
        if not await bm.has_session(p["id"]):
            return {"ok": False, "screenshot_b64": "", "url": "", "message": "No active browser session"}
        session = await bm.get_session(p["id"])
        img = await session.screenshot_b64()
        return {"ok": True, "screenshot_b64": img, "url": await session.current_url()}

    @app.post("/api/actions/browser-navigate")
    async def action_browser_navigate(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        url = body.get("url", "").strip()
        if not url:
            return {"ok": False, "screenshot_b64": "", "message": "url required"}
        from core.browser import browser_manager as bm
        session = await bm.get_session(p["id"])
        state = await session.navigate(url)
        return {"ok": True, "screenshot_b64": state["screenshot"], "url": state["url"]}

    @app.post("/api/actions/browser-click")
    async def action_browser_click(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager as bm
        if not await bm.has_session(p["id"]):
            return {"ok": False, "screenshot_b64": "", "message": "No active browser session"}
        session = await bm.get_session(p["id"])
        state = await session.click(int(body.get("x", 0)), int(body.get("y", 0)))
        return {"ok": True, "screenshot_b64": state["screenshot"], "url": state["url"]}

    @app.post("/api/actions/browser-type")
    async def action_browser_type(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager as bm
        if not await bm.has_session(p["id"]):
            return {"ok": False, "screenshot_b64": "", "message": "No active browser session"}
        session = await bm.get_session(p["id"])
        state = await session.type_text(body.get("text", ""))
        return {"ok": True, "screenshot_b64": state["screenshot"], "url": state["url"]}

    @app.post("/api/actions/browser-key")
    async def action_browser_key(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager as bm
        if not await bm.has_session(p["id"]):
            return {"ok": False, "screenshot_b64": "", "message": "No active browser session"}
        session = await bm.get_session(p["id"])
        state = await session.press_key(body.get("key", "Enter"))
        return {"ok": True, "screenshot_b64": state["screenshot"], "url": state["url"]}

    @app.post("/api/actions/browser-scroll")
    async def action_browser_scroll(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager as bm
        if not await bm.has_session(p["id"]):
            return {"ok": False, "screenshot_b64": "", "message": "No active browser session"}
        session = await bm.get_session(p["id"])
        state = await session.scroll(int(body.get("delta_y", 300)))
        return {"ok": True, "screenshot_b64": state["screenshot"], "url": state["url"]}

    @app.post("/api/actions/browser-back")
    async def action_browser_back(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager as bm
        if not await bm.has_session(p["id"]):
            return {"ok": False, "screenshot_b64": "", "message": "No active browser session"}
        session = await bm.get_session(p["id"])
        state = await session.go_back()
        return {"ok": True, "screenshot_b64": state["screenshot"], "url": state["url"]}

    @app.post("/api/actions/browser-save-session")
    async def action_browser_save_session(body: dict, token: str = ""):
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager as bm
        if not await bm.has_session(p["id"]):
            return {"ok": False, "message": "No active browser session"}
        session = await bm.get_session(p["id"])
        path = await session.save_storage_state()
        if path:
            cookies = await session.get_cookies()
            domains = list({c["domain"] for c in cookies})
            return {"ok": True, "message": f"Session saved ({len(cookies)} cookies)\nDomains: {', '.join(domains)}"}
        return {"ok": False, "message": "Failed to save session"}

    @app.post("/api/actions/browser-load-cookies")
    async def action_browser_load_cookies(body: dict, token: str = ""):
        """Load cookies from JSON array into current browser session."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        cookies = body.get("cookies")
        if not cookies or not isinstance(cookies, list):
            return {"ok": False, "message": "cookies array required"}
        from core.browser import browser_manager as bm
        session = await bm.get_session(p["id"])
        await session._ensure()
        try:
            await session._context.add_cookies(cookies)
            return {"ok": True, "message": f"{len(cookies)} cookies loaded into browser session"}
        except Exception as e:
            return {"ok": False, "message": f"Error: {e}"}

    @app.post("/api/actions/browser-get-text")
    async def action_browser_get_text(body: dict, token: str = ""):
        """Return the visible text content of the current browser page."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        from core.browser import browser_manager as bm
        if not await bm.has_session(p["id"]):
            return {"ok": False, "text": "", "message": "No active browser session"}
        session = await bm.get_session(p["id"])
        text = await session.get_page_text()
        url = await session.current_url()
        return {"ok": True, "text": text, "url": url, "message": f"{len(text)} chars extracted from {url}"}

    @app.post("/api/actions/gmail-list")
    async def action_gmail_list(body: dict, token: str = ""):
        """List Gmail messages."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None or not gmail_manager.is_connected(p["id"]):
            return {"ok": False, "message": "Gmail not connected"}
        query = body.get("query", "in:inbox is:unread")
        max_results = int(body.get("max", 50))
        try:
            msg_refs = await gmail_manager.list_messages(p["id"], query, max_results)
            messages = []
            for ref in msg_refs:
                try:
                    msg = await gmail_manager.get_message_summary(p["id"], ref["id"])
                    messages.append(msg)
                except Exception:
                    messages.append({"id": ref["id"], "error": "fetch failed"})
            return {"ok": True, "count": len(messages), "messages": messages}
        except Exception as e:
            return {"ok": False, "message": f"Error: {e}"}

    @app.post("/api/actions/gmail-get")
    async def action_gmail_get(body: dict, token: str = ""):
        """Get a Gmail message (headers + snippet + body)."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None or not gmail_manager.is_connected(p["id"]):
            return {"ok": False, "message": "Gmail not connected"}
        msg_id = body.get("id", "")
        if not msg_id:
            return {"ok": False, "message": "id is required"}
        try:
            msg = await gmail_manager.get_message_summary(p["id"], msg_id)
            return {"ok": True, **msg}
        except Exception as e:
            return {"ok": False, "message": f"Error: {e}"}

    @app.post("/api/actions/gmail-archive")
    async def action_gmail_archive(body: dict, token: str = ""):
        """Archive a Gmail message."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None or not gmail_manager.is_connected(p["id"]):
            return {"ok": False, "message": "Gmail not connected"}
        msg_id = body.get("id", "")
        if not msg_id:
            return {"ok": False, "message": "id is required"}
        try:
            await gmail_manager.archive_message(p["id"], msg_id)
            return {"ok": True, "archived": msg_id}
        except Exception as e:
            return {"ok": False, "message": f"Error: {e}"}

    @app.post("/api/actions/gmail-mark-read")
    async def action_gmail_mark_read(body: dict, token: str = ""):
        """Mark a Gmail message as read."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None or not gmail_manager.is_connected(p["id"]):
            return {"ok": False, "message": "Gmail not connected"}
        msg_id = body.get("id", "")
        if not msg_id:
            return {"ok": False, "message": "id is required"}
        try:
            import httpx as _httpx
            token_val = await gmail_manager._get_access_token(p["id"])
            async with _httpx.AsyncClient(timeout=10) as _c:
                r = await _c.post(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}/modify",
                    headers={"Authorization": f"Bearer {token_val}"},
                    json={"removeLabelIds": ["UNREAD"]},
                )
            r.raise_for_status()
            return {"ok": True, "marked_read": msg_id}
        except Exception as e:
            return {"ok": False, "message": f"Error: {e}"}

    @app.post("/api/actions/gmail-reply")
    async def action_gmail_reply(body: dict, token: str = ""):
        """Reply to a Gmail thread."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None or not gmail_manager.is_connected(p["id"]):
            return {"ok": False, "message": "Gmail not connected"}
        msg_id = body.get("id", "")
        to = body.get("to", "")
        subject = body.get("subject", "")
        content = body.get("body", "")
        if not msg_id or not to or not subject or not content:
            return {"ok": False, "message": "id, to, subject and body are required"}
        try:
            import httpx as _httpx
            import base64 as _b64
            from email.mime.text import MIMEText as _MIMEText
            # Get original for threading headers
            orig = await gmail_manager.get_message(p["id"], msg_id)
            orig_headers = {h["name"].lower(): h["value"] for h in orig.get("payload", {}).get("headers", [])}
            thread_id = orig.get("threadId", "")
            orig_msg_id = orig_headers.get("message-id", "")
            orig_refs = orig_headers.get("references", "")
            if not subject.startswith("Re:"):
                subject = f"Re: {subject}"
            msg = _MIMEText(content, "plain", "utf-8")
            msg["To"] = to
            msg["Subject"] = subject
            if orig_msg_id:
                msg["In-Reply-To"] = orig_msg_id
                msg["References"] = f"{orig_refs} {orig_msg_id}".strip() if orig_refs else orig_msg_id
            raw = _b64.urlsafe_b64encode(msg.as_bytes()).decode()
            payload = {"raw": raw}
            if thread_id:
                payload["threadId"] = thread_id
            token_val = await gmail_manager._get_access_token(p["id"])
            async with _httpx.AsyncClient(timeout=30) as _c:
                r = await _c.post(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                    headers={"Authorization": f"Bearer {token_val}"},
                    json=payload,
                )
            r.raise_for_status()
            result = r.json()
            return {"ok": True, "sent": result.get("id", ""), "thread_id": thread_id}
        except Exception as e:
            return {"ok": False, "message": f"Error: {e}"}

    @app.post("/api/actions/gmail-send")
    async def action_gmail_send(body: dict, token: str = ""):
        """Send an email via Gmail."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None or not gmail_manager.is_connected(p["id"]):
            return {"ok": False, "message": "Gmail not connected"}
        to = body.get("to", "")
        subject = body.get("subject", "")
        content = body.get("body", "")
        html = body.get("html", False)
        if not to or not subject or not content:
            return {"ok": False, "message": "to, subject and body are required"}
        try:
            result = await gmail_manager.send_message(p["id"], to, subject, content, html=html)
            return {"ok": True, "message": f"Email sent to {to}", "id": result.get("id", "")}
        except Exception as e:
            return {"ok": False, "message": f"Error: {e}"}

    @app.post("/api/actions/gmail-draft")
    async def action_gmail_draft(body: dict, token: str = ""):
        """Create a Gmail draft (not sent)."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        if gmail_manager is None or not gmail_manager.is_connected(p["id"]):
            return {"ok": False, "message": "Gmail not connected"}
        to = body.get("to", "")
        subject = body.get("subject", "")
        content = body.get("body", "")
        html = body.get("html", False)
        if not to or not subject or not content:
            return {"ok": False, "message": "to, subject and body are required"}
        try:
            result = await gmail_manager.create_draft(p["id"], to, subject, content, html=html)
            return {"ok": True, "message": f"Draft created for {to}", "id": result.get("id", "")}
        except Exception as e:
            return {"ok": False, "message": f"Error: {e}"}

    @app.post("/api/actions/send-email")
    async def action_send_email(body: dict, token: str = ""):
        """Send an email via Gmail (if connected) or SMTP fallback. Supports attachments."""
        p = parse_token(token)
        if not p:
            raise HTTPException(status_code=401)
        to = body.get("to", "")
        subject = body.get("subject", "")
        content = body.get("body", "")
        html = body.get("html", False)
        attachments = body.get("attachments")  # list of {filename, path} or {filename, content, mime_type}
        if not to or not subject or not content:
            return {"ok": False, "message": "to, subject and body are required"}
        if attachments and gmail_manager and gmail_manager.is_connected(p["id"]):
            try:
                result = await gmail_manager.send_message(p["id"], to, subject, content,
                                                          html=html, attachments=attachments)
                return {"ok": True, "method": "gmail", "message": f"Sent via Gmail to {to}", "id": result.get("id", "")}
            except Exception as e:
                return {"ok": False, "message": f"Gmail error: {e}"}
        from core.notifications import send_email_smart
        result = await send_email_smart(gmail_manager, connexion_manager, p["id"],
                                        to, subject, content, html=html)
        return result

    # ── Bash action (run-action blocks from chat) ─────────────

    @app.post("/api/actions/bash")
    async def action_bash(body: dict, token: str = ""):
        """Execute a shell command. Long commands (>5s) auto-run in background."""
        p = parse_token(token)
        if not p or (not p.get("admin") and not p.get("can_manage")):
            raise HTTPException(status_code=403)
        cmd = body.get("command", "").strip()
        if not cmd:
            return {"ok": False, "message": "command required"}
        background = body.get("background", False)
        timeout = int(body.get("timeout", 90))

        if background:
            # Run detached — return immediately
            log_path = f"/data/bash_bg_{abs(hash(cmd)) % 100000}.log"
            full_cmd = f"nohup sh -c {__import__('shlex').quote(cmd)} > {log_path} 2>&1 &"
            proc = await asyncio.create_subprocess_shell(
                full_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return {"ok": True, "message": f"Running in background. Log: {log_path}", "log": log_path}

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                return {"ok": False, "message": f"Timeout after {timeout}s. Use background:true for long commands."}

            out = stdout.decode(errors="replace").strip()
            err = stderr.decode(errors="replace").strip()
            ok = proc.returncode == 0
            result = out or err or "(no output)"
            if not ok:
                result = f"Exit {proc.returncode}\n{result}"
            return {"ok": ok, "message": result[:3000], "returncode": proc.returncode}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # ── Management actions (run-action blocks from chat) ───────

    @app.post("/api/actions/update-agent")
    async def action_update_agent(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if agents_manager is None:
            raise HTTPException(status_code=503)
        agent_id = body.pop("id", None) or body.pop("agent_id", None)
        if not agent_id:
            return {"ok": False, "message": "id required"}
        try:
            result = agents_manager.update_agent(agent_id, body)
            return {"ok": True, "message": f"Agent '{result.get('name', agent_id)}' updated"}
        except ValueError as e:
            return {"ok": False, "message": str(e)}

    @app.post("/api/actions/delete-agent")
    async def action_delete_agent(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        if agents_manager is None:
            raise HTTPException(status_code=503)
        agent_id = body.get("id")
        if not agent_id:
            return {"ok": False, "message": "id required"}
        try:
            agents_manager.delete_agent(agent_id)
            return {"ok": True, "message": f"Agent '{agent_id}' deleted"}
        except ValueError as e:
            return {"ok": False, "message": str(e)}

    @app.post("/api/actions/reload-skills")
    async def action_reload_skills(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        skills_manager.reload()
        skill_list = skills_manager.list_skills()
        return {"ok": True, "message": f"Skills reloaded: {', '.join(skill_list) if skill_list else 'none'}"}

    @app.post("/api/actions/update-skill")
    async def action_update_skill(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        name = body.get("name", "").strip()
        content = body.get("content", "")
        if not name:
            return {"ok": False, "message": "name required"}
        skill_path = skills_manager.skills_dir / name / "SKILL.md"
        if not skill_path.exists():
            return {"ok": False, "message": f"Skill '{name}' not found"}
        skill_path.write_text(content, encoding="utf-8")
        skills_manager.reload()
        return {"ok": True, "message": f"Skill '{name}' updated and reloaded"}

    @app.post("/api/actions/create-cron")
    async def action_create_cron(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not (p.get("admin") or p.get("can_manage")):
            raise HTTPException(status_code=403)
        cron_id = body.get("id")
        if not cron_id:
            return {"ok": False, "message": "id required"}
        try:
            scheduler.add_job_runtime(body)
            return {"ok": True, "message": f"Cron '{cron_id}' created"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    @app.post("/api/actions/update-cron")
    async def action_update_cron(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not (p.get("admin") or p.get("can_manage")):
            raise HTTPException(status_code=403)
        data = dict(body)
        cron_id = data.pop("id", None)
        if not cron_id:
            return {"ok": False, "message": "id required"}
        try:
            scheduler.update_job_runtime(cron_id, data)
            return {"ok": True, "message": f"Cron '{cron_id}' updated"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    @app.post("/api/actions/delete-cron")
    async def action_delete_cron(body: dict, token: str = ""):
        p = parse_token(token)
        if not p or not p.get("admin"):
            raise HTTPException(status_code=403)
        cron_id = body.get("id")
        if not cron_id:
            return {"ok": False, "message": "id required"}
        try:
            scheduler.remove_job_runtime(cron_id)
            return {"ok": True, "message": f"Cron '{cron_id}' deleted"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # ── WebSocket ─────────────────────────────────────────────

    @app.websocket("/ws/{user_id}")
    async def websocket_endpoint(websocket: WebSocket, user_id: str):
        await websocket.accept()

        # Auth handshake
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            msg = json.loads(raw)
            if msg.get("type") != "auth":
                await websocket.send_json({"type": "error", "message": "Auth required"})
                await websocket.close()
                return
            profile = parse_token(msg.get("token", ""))
            if not profile:
                await websocket.send_json({"type": "error", "message": "Unauthorized"})
                await websocket.close()
                return
        except (asyncio.TimeoutError, json.JSONDecodeError):
            await websocket.send_json({"type": "error", "message": "Auth timeout"})
            await websocket.close()
            return

        if not profile.get("admin") and profile["id"] != user_id:
            await websocket.send_json({"type": "error", "message": "Forbidden"})
            await websocket.close()
            return

        await websocket.send_json({
            "type": "connected",
            "user_id": user_id,
            "profile": {
                "id": profile["id"],
                "name": profile.get("name", profile["id"]),
                "admin": profile.get("admin", False),
            },
        })

        history = memory.get_history(user_id)
        if history:
            await websocket.send_json({"type": "history", "messages": history})

        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)

                if data.get("type") != "message":
                    continue

                user_text = data.get("content", "").strip()
                attachments = data.get("attachments", [])

                if not user_text and not attachments:
                    continue

                # Process text file attachments → prepend to user_text
                image_blocks = []
                for att in attachments:
                    name = att.get("name", "file")
                    if att.get("is_image"):
                        image_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": att.get("mime_type", "image/jpeg"),
                                "data": att.get("data", ""),
                            },
                        })
                    else:
                        content = att.get("content", "")
                        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                        fence = f"```{ext}\n{content}\n```" if ext else f"```\n{content}\n```"
                        user_text = f"<file name=\"{name}\">\n{fence}\n</file>\n\n" + user_text

                # What to store in memory (text only — images stored as placeholder)
                memory_text = user_text
                if image_blocks:
                    img_names = [a["name"] for a in attachments if a.get("is_image")]
                    memory_text = f"[Images attached: {', '.join(img_names)}]\n" + user_text

                await memory.add_message(user_id, "user", memory_text)
                await websocket.send_json({"type": "user_message", "content": memory_text})

                # Build message list — replace last user message with multimodal if needed
                messages = memory.get_history(user_id)
                if image_blocks:
                    multimodal_content = image_blocks + ([{"type": "text", "text": user_text}] if user_text else [])
                    messages = messages[:-1] + [{"role": "user", "content": multimodal_content}]

                # Per-user context injection (always inject at least profile info)
                gmail_connected = gmail_manager is not None and gmail_manager.is_connected(user_id)
                profile_ctx = f"The current user is: {profile.get('name', user_id)}"
                if profile.get("admin"):
                    profile_ctx += " (admin)"

                # Inject persistent user memory (facts learned across conversations)
                memory_content = user_memory.get(user_id) if user_memory is not None else ""
                if memory_content:
                    profile_ctx += f"\n\n[Mémoire persistante — faits appris sur cet utilisateur]\n{memory_content}"

                if context_manager is not None:
                    custom_ctx = context_manager.build_context_prompt(user_id)
                    context_addition = profile_ctx + ("\n\n" + custom_ctx if custom_ctx else "")
                else:
                    context_addition = profile_ctx

                # Inject configured connexion capabilities so Claude knows what's available
                if connexion_manager is not None:
                    caps = []
                    tg = connexion_manager.get_telegram(user_id)
                    if tg.get("enabled") and tg.get("bot_token"):
                        caps.append("Telegram (you can send notifications by emitting a send-notification block)")
                    em = connexion_manager.get_email(user_id)
                    if em.get("enabled") and em.get("smtp_host"):
                        caps.append("Email SMTP (you can send emails by emitting a send-notification block)")
                    if gmail_connected:
                        caps.append("Gmail (connected — you can read, archive and send emails)")
                    notion = connexion_manager.get_notion(user_id)
                    if notion.get("enabled") and notion.get("api_key"):
                        notion_cap = "Notion (connecté — utilise l'API REST Notion directement."
                        notion_cap += f" Token: {notion['api_key']}."
                        if notion.get("database_id"):
                            notion_cap += f" Database ID tableau influenceurs: {notion['database_id']}."
                        if notion.get("linkedin_db_id"):
                            notion_cap += f" Database ID posts LinkedIn: {notion['linkedin_db_id']}."
                        notion_cap += " Endpoint: https://api.notion.com/v1/ — header requis: 'Notion-Version: 2022-06-28'.)"
                        caps.append(notion_cap)
                    if caps:
                        context_addition += "\n\nAvailable connexions for this user: " + ", ".join(caps) + "."

                # Detect agent by keyword (shared across all users)
                detected_agent = None
                if agents_manager is not None:
                    detected_agent = agents_manager.detect_agent(user_text)

                await websocket.send_json({"type": "stream_start"})
                full_response = ""
                input_tokens = 0
                output_tokens = 0

                try:
                    if detected_agent:
                        await websocket.send_json({
                            "type": "agent_start",
                            "agent_id": detected_agent["id"],
                            "agent_name": detected_agent["name"],
                            "agent_avatar": detected_agent["avatar"],
                        })

                        agent_system = detected_agent["system_prompt"]
                        if context_addition:
                            agent_system += "\n\n" + context_addition

                        async for event in agent_backend.stream(messages, agent_system):
                            if event["type"] == "chunk":
                                full_response += event["content"]
                                await websocket.send_json({"type": "chunk", "content": event["content"]})

                        await memory.add_message(user_id, "assistant", full_response)
                        await websocket.send_json({
                            "type": "stream_end",
                            "content": full_response,
                            "usage": None,
                            "agent_id": detected_agent["id"],
                            "agent_name": detected_agent["name"],
                            "agent_avatar": detected_agent["avatar"],
                        })

                    else:
                        # Chat path only: inject browser + management capabilities
                        chat_extra = ""

                        try:
                            from core.browser import browser_manager as _bm
                            _has_br = await _bm.has_session(user_id)
                            logger.info(f"[WS] browser has_session({user_id}) = {_has_br}")
                            if _has_br:
                                _sess = await _bm.get_session(user_id)
                                _cur_url = await _sess.current_url()
                                logger.info(f"[WS] browser URL for {user_id}: {_cur_url}")
                                chat_extra += (
                                    f"\n\nBrowser session active (current URL: {_cur_url or 'about:blank'})."
                                    "\nYou can interact with the browser using run-action blocks:"
                                    "\n  Take screenshot: ```run-action\n{\"action\": \"browser-screenshot\"}\n```"
                                    "\n  Navigate: ```run-action\n{\"action\": \"browser-navigate\", \"url\": \"https://...\"}\n```"
                                    "\n  Click: ```run-action\n{\"action\": \"browser-click\", \"x\": 100, \"y\": 200}\n```"
                                    "\n  Type text: ```run-action\n{\"action\": \"browser-type\", \"text\": \"hello\"}\n```"
                                    "\n  Press key: ```run-action\n{\"action\": \"browser-key\", \"key\": \"Enter\"}\n```"
                                    "\n  Scroll: ```run-action\n{\"action\": \"browser-scroll\", \"delta_y\": 300}\n```"
                                    "\n  Go back: ```run-action\n{\"action\": \"browser-back\"}\n```"
                                    "\n  Save session (cookies + localStorage): ```run-action\n{\"action\": \"browser-save-session\"}\n```"
                                    "\n  Get page text: ```run-action\n{\"action\": \"browser-get-text\"}\n```"
                                    "\nScreenshots are displayed inline in the chat."
                                    "\nWhen asked to save the session or remember the login, emit browser-save-session."
                                    "\nWhen asked to read/extract/analyze the page content, emit browser-get-text."
                                )
                        except Exception as _e:
                            logger.warning(f"[WS] browser injection error for {user_id}: {_e}")

                        if gmail_connected:
                            chat_extra += (
                                "\n\nGmail connected. You can send emails via run-action:"
                                "\n  ```run-action\n{\"action\": \"gmail-send\", \"to\": \"email@example.com\","
                                " \"subject\": \"Subject\", \"body\": \"Email body here\"}\n```"
                                "\n  Set \"html\": true for HTML emails."
                            )
                        # send-email is always available (Gmail or SMTP fallback)
                        _em = connexion_manager.get_email(user_id) if connexion_manager else {}
                        if gmail_connected or (_em.get("enabled") and _em.get("smtp_host")):
                            chat_extra += (
                                "\n\nYou can send an email to any address via run-action (uses Gmail if connected, SMTP otherwise):"
                                "\n  ```run-action\n{\"action\": \"send-email\", \"to\": \"recipient@example.com\","
                                " \"subject\": \"Subject\", \"body\": \"Body text here\"}\n```"
                                "\n  Omit \"to\" to send to the user's own address. Set \"html\": true for HTML."
                            )

                        # Memory instructions (always)
                        if user_memory is not None:
                            chat_extra += (
                                "\n\nWhen the user asks you to remember something (\"retiens que\", \"souviens-toi\", \"remember that\", etc.), "
                                "immediately emit a remember block — no confirmation needed:\n"
                                "  ```run-action\n{\"action\": \"remember\", \"fact\": \"the fact to remember\"}\n```\n"
                                "When asked to forget something:\n"
                                "  ```run-action\n{\"action\": \"forget\", \"keyword\": \"keyword to search and remove\"}\n```\n"
                                "Always use the user's language in the fact. Be concise and factual (e.g. \"Youri Michel est le beau-frère de Gilles\")."
                            )

                        _can_manage = profile.get("admin") or profile.get("can_manage")
                        if _can_manage and agents_manager is not None:
                            _agents = agents_manager.list_agents()
                            _skills = skills_manager.list_skills()
                            _crons = scheduler.get_status()
                            _agent_list = ", ".join(f"{a['name']} (id:{a['id']})" for a in _agents) or "none"
                            _skill_list = ", ".join(_skills) or "none"
                            _cron_list = ", ".join(f"{c['name']} (id:{c['id']})" for c in _crons) or "none"
                            chat_extra += (
                                f"\n\n[Management] You can create automations via run-action blocks."
                                f"\nAgents ({len(_agents)}): {_agent_list}"
                                f"\nSkills ({len(_skills)}): {_skill_list}"
                                f"\nCrons ({len(_crons)}): {_cron_list}"
                                "\n  create-cron: {\"action\":\"create-cron\",\"id\":\"...\",\"name\":\"...\",\"schedule\":\"0 9 * * *\",\"command\":\"...\"}"
                                "\n  update-cron: {\"action\":\"update-cron\",\"id\":\"...\",\"schedule\":\"...\",\"command\":\"...\"}"
                                "\n  create-agent: {\"action\":\"create-agent\",\"name\":\"...\",\"system_prompt\":\"...\",\"triggers\":[...]}"
                                "\n  create-skill: {\"action\":\"create-skill\",\"name\":\"...\",\"skill_md\":\"...\"}"
                            )
                            if profile.get("admin"):
                                chat_extra += (
                                    "\n  update-agent: {\"action\":\"update-agent\",\"id\":\"...\",\"name\":\"...\",\"system_prompt\":\"...\"}"
                                    "\n  delete-agent: {\"action\":\"delete-agent\",\"id\":\"...\"}"
                                    "\n  update-skill: {\"action\":\"update-skill\",\"name\":\"...\",\"content\":\"...full SKILL.md...\"}"
                                    "\n  reload-skills: {\"action\":\"reload-skills\"}"
                                    "\n  update-cron: {\"action\":\"update-cron\",\"id\":\"...\",...fields}"
                                    "\n  delete-cron: {\"action\":\"delete-cron\",\"id\":\"...\"}"
                                )

                        skills_prompt = skills_manager.build_system_prompt()
                        base = (context_addition + chat_extra) if (context_addition or chat_extra) else ""
                        if base:
                            system_prompt = base + ("\n\n" + skills_prompt if skills_prompt else "")
                        else:
                            system_prompt = skills_prompt

                        async for event in chat_backend.stream(messages, system_prompt):
                            if event["type"] == "chunk":
                                full_response += event["content"]
                                await websocket.send_json({"type": "chunk", "content": event["content"]})
                            elif event["type"] == "usage":
                                input_tokens = event.get("input_tokens", 0)
                                output_tokens = event.get("output_tokens", 0)

                        if input_tokens or output_tokens:
                            await usage_tracker.record(user_id, chat_backend.model, input_tokens, output_tokens)
                            from core.usage import compute_cost, EUR_RATE
                            cost_usd = compute_cost(chat_backend.model, input_tokens, output_tokens)
                            usage_info = {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "cost_usd": round(cost_usd, 6),
                                "cost_eur": round(cost_usd * EUR_RATE, 6),
                            }
                        else:
                            usage_info = None

                        await memory.add_message(user_id, "assistant", full_response)
                        await websocket.send_json({
                            "type": "stream_end",
                            "content": full_response,
                            "usage": usage_info,
                        })

                except Exception as e:
                    logger.error(f"Backend error: {e}")
                    await websocket.send_json({"type": "error", "message": str(e)})

        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: {user_id}")
        except Exception as e:
            logger.error(f"WebSocket error for {user_id}: {e}")

    return app
