"""
openNoClaw — main entrypoint.
"""

import asyncio
import logging
import sys
from pathlib import Path

import uvicorn
import yaml

from core.agent import AnthropicSDKBackend, ClaudeCliBackend
from core.memory import Memory
from core.skills import SkillsManager
from core.scheduler import Scheduler
from core.usage import UsageTracker
from core.auth_manager import ClaudeAuthManager
from core.agents_manager import AgentsManager
from core.context_manager import ContextManager
from core.connexion_manager import ConnexionManager
from core.gmail_manager import GmailManager
from core.google_tasks_manager import GoogleTasksManager
from core.user_memory import UserMemory
from interfaces.web_server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("openNoClaw")


def load_config(path: str = "config.yml") -> dict:
    cfg_path = Path(path)
    if not cfg_path.exists():
        logger.error(f"Config file not found: {path}")
        logger.error("Run: cp config.yml.example config.yml")
        sys.exit(1)
    with open(cfg_path) as f:
        return yaml.safe_load(f)


async def main():
    config = load_config()

    backend_cfg = config.get("backend", {})
    model = backend_cfg.get("model", "claude-sonnet-4-6")
    api_key = backend_cfg.get("api_key", "")

    # Chat always goes through Anthropic SDK (BYOK, pay-per-token — CGU compliant)
    chat_backend = AnthropicSDKBackend(api_key=api_key, model=model)
    # Agents/skills always use claude -p subprocess (Claude.ai subscription)
    agent_backend = ClaudeCliBackend(model=model)

    logger.info(f"Chat: {chat_backend.type} / {chat_backend.model}")
    logger.info(f"Agents: {agent_backend.type} / {agent_backend.model}")

    memory_cfg = config.get("memory", {})
    memory = Memory(
        path=memory_cfg.get("path", "./data/memory.json"),
        max_messages=memory_cfg.get("max_messages", 50),
    )

    usage_tracker = UsageTracker(path="./data/usage.json")

    skills_dir = config.get("skills_dir", "./skills")
    skills_manager = SkillsManager(skills_dir=skills_dir)
    logger.info(f"Skills: {skills_manager.list_skills() or ['none']}")

    profiles = config.get("profiles", [])
    if profiles:
        names = [p.get("name", p["id"]) for p in profiles]
        logger.info(f"Profiles: {names}")
    else:
        logger.info("Profiles: default (no auth)")

    data_dir = config.get("data_dir", "/data")
    agents_manager = AgentsManager(path=f"{data_dir}/agents.json")
    context_manager = ContextManager(data_dir=data_dir)
    connexion_manager = ConnexionManager(data_dir=data_dir)
    gmail_manager = GmailManager(data_dir=data_dir)
    google_tasks_manager = GoogleTasksManager(data_dir=data_dir)
    user_memory = UserMemory(data_dir=data_dir)

    scheduler = Scheduler(config_path="config.yml")
    crons = config.get("crons") or []
    if crons:
        scheduler.load_jobs(crons)
    scheduler.set_agent_backend(agents_manager, agent_backend)
    scheduler.set_connexion_manager(connexion_manager)
    scheduler.start()

    auth_manager = ClaudeAuthManager()

    web_cfg = config.get("web", {})
    app = create_app(
        chat_backend, agent_backend, memory, skills_manager, scheduler,
        usage_tracker, config, auth_manager, agents_manager, context_manager,
        connexion_manager, gmail_manager, google_tasks_manager,
        config_path="config.yml", user_memory=user_memory,
    )

    host = web_cfg.get("host", "0.0.0.0")
    port = web_cfg.get("port", 8080)

    server_config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(server_config)

    tg_cfg = config.get("telegram", {})
    telegram_bot = None
    if tg_cfg.get("enabled") and tg_cfg.get("token"):
        from interfaces.telegram_bot import TelegramBot
        telegram_bot = TelegramBot(
            token=tg_cfg["token"],
            backend=chat_backend,
            memory=memory,
            skills_manager=skills_manager,
            allowed_chat_ids=tg_cfg.get("allowed_chat_ids", []),
        )
        await telegram_bot.start()
        logger.info("Telegram bot enabled")

    logger.info(f"Web UI: http://{host if host != '0.0.0.0' else 'localhost'}:{port}")

    try:
        await server.serve()
    finally:
        scheduler.stop()
        if telegram_bot:
            await telegram_bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped")
