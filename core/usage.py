"""
Usage tracker — records token consumption and computes cost per profile.
Pricing table is approximate; update as needed.
"""

import json
import asyncio
from datetime import date, datetime
from pathlib import Path

# Prices in USD per 1M tokens
MODEL_PRICING: dict[str, dict] = {
    "claude-haiku-4-5-20251001":   {"input": 0.80,  "output": 4.00},
    "claude-haiku-3-5-20241022":   {"input": 0.80,  "output": 4.00},
    "claude-sonnet-4-6":           {"input": 3.00,  "output": 15.00},
    "claude-sonnet-3-7-20250219":  {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":             {"input": 15.00, "output": 75.00},
    # default fallback
    "_default":                    {"input": 3.00,  "output": 15.00},
}

EUR_RATE = 0.92  # approximate USD → EUR


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return cost in USD."""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["_default"])
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


class UsageTracker:
    def __init__(self, path: str = "./data/usage.json"):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict = {}
        self._load()

    def _load(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                with open(self.path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}

    async def _save(self):
        async with self._lock:
            with open(self.path, "w") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)

    async def record(self, profile_id: str, model: str, input_tokens: int, output_tokens: int):
        """Record a usage event."""
        cost_usd = compute_cost(model, input_tokens, output_tokens)
        today = date.today().isoformat()

        if profile_id not in self._data:
            self._data[profile_id] = {}

        if today not in self._data[profile_id]:
            self._data[profile_id][today] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "calls": 0,
            }

        day = self._data[profile_id][today]
        day["input_tokens"] += input_tokens
        day["output_tokens"] += output_tokens
        day["cost_usd"] += cost_usd
        day["calls"] += 1

        await self._save()

    def get_stats(self, profile_id: str) -> dict:
        """Return today's stats and all-time total for a profile."""
        profile_data = self._data.get(profile_id, {})
        today = date.today().isoformat()

        today_data = profile_data.get(today, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0})

        total_usd = sum(d["cost_usd"] for d in profile_data.values())
        total_tokens = sum(d["input_tokens"] + d["output_tokens"] for d in profile_data.values())
        total_calls = sum(d["calls"] for d in profile_data.values())

        return {
            "today": {
                "cost_usd": round(today_data["cost_usd"], 5),
                "cost_eur": round(today_data["cost_usd"] * EUR_RATE, 5),
                "input_tokens": today_data["input_tokens"],
                "output_tokens": today_data["output_tokens"],
                "calls": today_data["calls"],
            },
            "total": {
                "cost_usd": round(total_usd, 4),
                "cost_eur": round(total_usd * EUR_RATE, 4),
                "tokens": total_tokens,
                "calls": total_calls,
            },
            "history": {
                day: {
                    "cost_usd": round(d["cost_usd"], 5),
                    "calls": d["calls"],
                }
                for day, d in sorted(profile_data.items(), reverse=True)[:7]
            },
        }

    def get_all_stats(self) -> dict:
        """Return stats for all profiles (admin view)."""
        return {pid: self.get_stats(pid) for pid in self._data}
