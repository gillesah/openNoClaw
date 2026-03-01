"""
Skills loader — scans skills/*/SKILL.md and builds a system prompt.
Claude decides which skill to invoke based on the combined prompt.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SkillsManager:
    def __init__(self, skills_dir: str = "./skills"):
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, str] = {}  # name -> SKILL.md content
        self.reload()

    def reload(self):
        """Scan skills_dir for SKILL.md files and load them."""
        self._skills = {}
        if not self.skills_dir.exists():
            logger.warning(f"Skills directory not found: {self.skills_dir}")
            return

        for skill_md in sorted(self.skills_dir.glob("*/SKILL.md")):
            name = skill_md.parent.name
            try:
                content = skill_md.read_text(encoding="utf-8")
                self._skills[name] = content
                logger.info(f"Loaded skill: {name}")
            except OSError as e:
                logger.error(f"Failed to load skill {name}: {e}")

    def build_system_prompt(self, base_prompt: str = "") -> str:
        """Concatenate all SKILL.md files into a single system prompt."""
        parts = []

        if base_prompt:
            parts.append(base_prompt)

        if self._skills:
            parts.append("## Available Skills\n")
            parts.append(
                "You have access to the following skills. "
                "Use them when appropriate based on the user's request.\n"
            )
            for name, content in self._skills.items():
                parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n".join(parts)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    def get_skill(self, name: str) -> str | None:
        return self._skills.get(name)

    def get_skill_script(self, name: str) -> str | None:
        """Return skill.py content if it exists."""
        path = self.skills_dir / name / "skill.py"
        try:
            return path.read_text(encoding="utf-8") if path.exists() else None
        except OSError:
            return None

    def update_skill_script(self, name: str, content: str):
        """Write (or create) skill.py for a skill."""
        skill_dir = self.skills_dir / name
        if not skill_dir.exists():
            raise FileNotFoundError(f"Skill directory not found: {name}")
        (skill_dir / "skill.py").write_text(content, encoding="utf-8")
