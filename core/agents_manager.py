"""AgentsManager — CRUD for AI agents with keyword-based routing."""

import json
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_AGENTS = [
    {
        "id": "seo-bot",
        "name": "SEO Bot",
        "description": "Analyse et optimise le référencement de vos sites",
        "avatar": "robot-seo",
        "model": "claude-sonnet-4-6",
        "system_prompt": (
            "Tu es un expert SEO. Analyse le contenu, les meta-tags, la structure des URLs "
            "et fournis des recommandations concrètes pour améliorer le référencement naturel. "
            "Sois précis et actionnable dans tes conseils."
        ),
        "triggers": ["SEO", "référencement", "meta", "google", "positionnement", "ranking", "serp"],
        "skills": [],
        "enabled": True,
    },
    {
        "id": "community-manager",
        "name": "Community Bot",
        "description": "Rédige et publie du contenu sur les réseaux sociaux",
        "avatar": "robot-cm",
        "model": "claude-sonnet-4-6",
        "system_prompt": (
            "Tu es un community manager expert. Tu crées du contenu engageant pour Reddit, "
            "LinkedIn, Twitter/X et autres réseaux sociaux. Tu adaptes le ton à chaque plateforme, "
            "connais les codes de chaque communauté et optimises pour l'engagement."
        ),
        "triggers": ["reddit", "linkedin", "post", "publication", "réseau", "social", "tweet", "contenu", "communauté"],
        "skills": [],
        "enabled": True,
    },
    {
        "id": "dev-bot",
        "name": "Dev Bot",
        "description": "Code review, debug, architecture",
        "avatar": "robot-dev",
        "model": "claude-sonnet-4-6",
        "system_prompt": (
            "Tu es un développeur senior expert en Python, TypeScript, Kotlin et Vue 3. "
            "Tu analyses le code, identifies les bugs, proposes des architectures solides "
            "et effectues des code reviews détaillées. Tu privilégies la lisibilité et la maintenabilité."
        ),
        "triggers": ["bug", "code", "erreur", "debug", "refactor", "architecture", "typescript", "python", "kotlin", "crash"],
        "skills": [],
        "enabled": True,
    },
]


class AgentsManager:
    def __init__(self, path: str = "/data/agents.json"):
        self._path = Path(path)
        self._agents: list[dict] = []
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                self._agents = json.loads(self._path.read_text())
                logger.info(f"Agents: {len(self._agents)} loaded from {self._path}")
                return
            except Exception as e:
                logger.error(f"Failed to load agents: {e}")
        # Create defaults
        self._agents = [dict(a) for a in DEFAULT_AGENTS]
        self._save()
        logger.info(f"Agents: created {len(self._agents)} defaults at {self._path}")

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._agents, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Failed to save agents: {e}")

    def list_agents(self) -> list[dict]:
        return list(self._agents)

    def get_agent(self, agent_id: str) -> dict | None:
        return next((a for a in self._agents if a["id"] == agent_id), None)

    def create_agent(self, data: dict) -> dict:
        if not data.get("id"):
            data["id"] = str(uuid.uuid4())[:8]
        if any(a["id"] == data["id"] for a in self._agents):
            raise ValueError(f"Agent ID already exists: {data['id']}")
        agent = {
            "id": data["id"],
            "name": data.get("name", "Agent"),
            "description": data.get("description", ""),
            "avatar": data.get("avatar", "robot-default"),
            "model": data.get("model", "claude-sonnet-4-6"),
            "system_prompt": data.get("system_prompt", ""),
            "triggers": data.get("triggers", []),
            "skills": data.get("skills", []),
            "enabled": data.get("enabled", True),
        }
        self._agents.append(agent)
        self._save()
        return agent

    def update_agent(self, agent_id: str, data: dict) -> dict:
        for i, a in enumerate(self._agents):
            if a["id"] == agent_id:
                self._agents[i] = {**a, **data, "id": agent_id}
                self._save()
                return self._agents[i]
        raise ValueError(f"Agent not found: {agent_id}")

    def delete_agent(self, agent_id: str):
        before = len(self._agents)
        self._agents = [a for a in self._agents if a["id"] != agent_id]
        if len(self._agents) == before:
            raise ValueError(f"Agent not found: {agent_id}")
        self._save()

    def detect_agent(self, message: str) -> dict | None:
        """Keyword matching on triggers (case-insensitive). Returns first enabled match."""
        msg_lower = message.lower()
        for agent in self._agents:
            if not agent.get("enabled", True):
                continue
            for trigger in agent.get("triggers", []):
                if trigger.lower() in msg_lower:
                    return agent
        return None
