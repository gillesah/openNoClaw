"""
AI backends for openNoClaw.

ClaudeCliBackend    — uses `claude -p` subprocess (Claude.ai subscription, no extra cost)
AnthropicSDKBackend — direct Anthropic API calls with streaming + token usage
"""

import asyncio
import os
from typing import AsyncIterator

import anthropic


class ClaudeCliBackend:
    """
    Calls `claude -p` as a subprocess.
    Requires Claude Code CLI installed and authenticated.
    Free with a Claude.ai subscription (forfait).
    """

    type = "claude-cli"

    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model

    def _base_cmd(self, prompt: str, model: str) -> list[str]:
        return [
            "claude", "-p", prompt,
            "--model", model,
            "--dangerously-skip-permissions",
        ]

    async def _run_cmd(self, cmd: list[str]) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode().strip()
                raise RuntimeError(f"claude CLI error (code {proc.returncode}): {err}")
            return stdout.decode().strip()
        except FileNotFoundError:
            raise RuntimeError(
                "claude CLI not found. Install: npm install -g @anthropic-ai/claude-code"
            )

    async def chat(self, messages: list[dict], system: str = "") -> tuple[str, dict]:
        """Returns (response_text, usage_dict). Usage is empty for CLI."""
        prompt_parts = []
        if system:
            prompt_parts.append(f"<system>\n{system}\n</system>\n")
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                prompt_parts.append(f"Human: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
        prompt = "\n\n".join(prompt_parts)
        response = await self._run_cmd(self._base_cmd(prompt, self.model))
        return response, {}

    async def stream(self, messages: list[dict], system: str = "") -> AsyncIterator[dict]:
        """Yields chunks then a final usage event (empty for CLI)."""
        response, _ = await self.chat(messages, system)
        yield {"type": "chunk", "content": response}
        yield {"type": "usage", "input_tokens": 0, "output_tokens": 0}

    async def run_agent(self, system_prompt: str, task: str, model: str | None = None) -> str:
        """Execute a one-shot agent task via claude -p. Used by cron jobs."""
        prompt_parts = []
        if system_prompt:
            prompt_parts.append(f"<system>\n{system_prompt}\n</system>")
        prompt_parts.append(f"Human: {task}")
        prompt = "\n\n".join(prompt_parts)
        return await self._run_cmd(self._base_cmd(prompt, model or self.model))


class AnthropicSDKBackend:
    """
    Direct Anthropic API calls. Supports native streaming + token counting.
    """

    type = "anthropic-api"

    def __init__(self, api_key: str = "", model: str = "claude-sonnet-4-6"):
        self.model = model
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "") or self._load_key_file()
        self._key = key
        self.client = anthropic.AsyncAnthropic(api_key=key) if key else None

    @staticmethod
    def _load_key_file() -> str:
        from pathlib import Path
        p = Path("/data/api_key.txt")
        return p.read_text().strip() if p.exists() else ""

    def set_api_key(self, key: str):
        self._key = key
        self.client = anthropic.AsyncAnthropic(api_key=key)

    def _require_client(self):
        if not self.client:
            raise RuntimeError(
                "No Anthropic API key configured. Add it in Settings."
            )

    async def chat(self, messages: list[dict], system: str = "") -> tuple[str, dict]:
        """Returns (response_text, usage_dict)."""
        self._require_client()
        kwargs = dict(model=self.model, max_tokens=4096, messages=messages)
        if system:
            kwargs["system"] = system
        response = await self.client.messages.create(**kwargs)
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return response.content[0].text, usage

    async def stream(self, messages: list[dict], system: str = "") -> AsyncIterator[dict]:
        """Yields text chunks, then a final usage event."""
        self._require_client()
        kwargs = dict(model=self.model, max_tokens=4096, messages=messages)
        if system:
            kwargs["system"] = system

        async with self.client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield {"type": "chunk", "content": text}
            final = await stream.get_final_message()
            yield {
                "type": "usage",
                "input_tokens": final.usage.input_tokens,
                "output_tokens": final.usage.output_tokens,
            }


def build_backend(config: dict):
    btype = config.get("type", "claude-cli")
    model = config.get("model", "claude-sonnet-4-6")
    api_key = config.get("api_key", "")
    if btype == "anthropic-api":
        return AnthropicSDKBackend(api_key=api_key, model=model)
    return ClaudeCliBackend(model=model)
