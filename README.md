# openNoClaw

A lightweight, self-hosted AI home agent built around the Anthropic Claude API or Claude Code CLI.

**Features:**
- Web chat UI with real-time streaming (WebSocket)
- Telegram bot integration
- Cron job scheduler with live dashboard
- Skill system — drop in `SKILL.md` files, Claude reads them automatically
- Two AI backends: Claude CLI (free with Claude.ai subscription) or Anthropic API (direct)
- Zero frontend dependencies — vanilla HTML/CSS/JS

## Quick start

```bash
git clone https://github.com/yourusername/openNoClaw
cd openNoClaw

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.yml.example config.yml
# Edit config.yml — set your backend, password, Telegram token, etc.

python main.py
# → Open http://localhost:8080
```

## Configuration

```yaml
backend:
  type: claude-cli        # claude-cli | anthropic-api
  api_key: ""             # only for anthropic-api
  model: claude-sonnet-4-6

web:
  host: 0.0.0.0
  port: 8080
  auth:
    enabled: true
    password: yourpassword

telegram:
  enabled: false
  token: "your-bot-token"
  allowed_chat_ids: [123456789]

memory:
  max_messages: 50
  path: ./data/memory.json

crons:
  - id: my-daily-task
    name: "Daily task"
    schedule: "0 9 * * *"
    command: "python skills/my-skill/run.py"
    enabled: true

skills_dir: ./skills
```

## Backends

### `claude-cli` (default)
Uses the [Claude Code CLI](https://github.com/anthropics/claude-code) (`claude -p`).
- Requires `npm install -g @anthropic-ai/claude-code` and login
- Free if you have a Claude.ai subscription
- No streaming (full response at once)

### `anthropic-api`
Direct Anthropic API calls.
- Requires an API key from [console.anthropic.com](https://console.anthropic.com)
- Supports native streaming
- ~$2–5/month for personal use

## Skills

Create a directory under `skills/` with a `SKILL.md` file:

```
skills/
  my-skill/
    SKILL.md      ← describes capabilities to Claude
    my_script.py  ← optional Python script
```

Claude reads all `SKILL.md` files as part of its system prompt and decides which skill to use based on the user's message.

## Crons

Define cron jobs in `config.yml`. The dashboard shows last run status and next scheduled time.

```yaml
crons:
  - id: sync-job
    name: "Data sync"
    schedule: "*/10 * * * *"   # every 10 minutes
    command: "python skills/my-skill/sync.py"
    enabled: true
```

## Telegram

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Set `telegram.token` in config
3. Add your chat ID to `allowed_chat_ids` (get it from [@userinfobot](https://t.me/userinfobot))

**Bot commands:**
- `/start` — say hello
- `/clear` — clear conversation history
- `/skills` — list loaded skills

## Your keys, your instance

openNoClaw never includes any API keys or credentials. Each person who deploys it provides their own:

- **Anthropic API backend** → your own API key from [console.anthropic.com](https://console.anthropic.com), entered in the Settings UI
- **Claude Code CLI backend** → your own Claude.ai subscription, authenticated via the Settings UI (one-click OAuth flow)

This follows the same model as self-hosted tools like Nextcloud or Gitea: the code is shared, the credentials are yours alone.

**Household use** (e.g. two people on the same home server) is fine with a single subscription. Each person who runs their own separate instance should use their own subscription/API key.

## License

MIT
