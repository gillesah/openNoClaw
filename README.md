# openNoClaw

A self-hosted AI home agent for non-developers — web interface, skills, crons, and multi-user support.

Built around [Claude Code CLI](https://github.com/anthropics/claude-code) and the Anthropic API, openNoClaw lets you run a personal AI assistant on your own server, fully compliant with Anthropic's usage policies.

> Inspired by the spirit of [OpenClaw](https://github.com/openClaw) but rebuilt from scratch: no API workarounds, no unofficial hacks — just the official Claude Code CLI and Anthropic API, wrapped in a friendly web UI anyone in your household can use.

---

## What it does

- **Chat UI** — web interface with real-time streaming, conversation history, multi-session support
- **Skill system** — drop a `SKILL.md` file in `skills/my-skill/` and Claude reads it automatically
- **Cron scheduler** — automate tasks on a schedule, with Telegram/email notifications
- **Multi-user** — one server, multiple users, each with their own connexions and history
- **Connexions** — configure Telegram, Gmail, GitHub, Linear, Notion, social networks from the Settings UI
- **Browser** — embedded Playwright browser, controllable from the chat
- **Custom agents** — create specialized agents with their own system prompts and triggers
- **Telegram bot** — chat with your assistant directly from Telegram
- **Zero frontend dependencies** — vanilla HTML/CSS/JS, nothing to build

---

## Quick start

**Requirements:** Python 3.11+, Docker (recommended)

```bash
git clone https://github.com/gillesah/openNoClaw
cd openNoClaw

cp config.yml.example config.yml
# Edit config.yml — set your users, API key or CLI backend, etc.
```

### Option A — Docker (recommended)

```bash
# Set your Claude config dir (where Claude Code stores its auth)
export CLAUDE_CONFIG_DIR=~/.claude

docker compose up -d
# → Open http://localhost:8080
```

### Option B — Local Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
# → Open http://localhost:8080
```

---

## Configuration

```yaml
# config.yml
backend:
  type: claude-cli        # claude-cli | anthropic-api
  api_key: ""             # only for anthropic-api
  model: claude-sonnet-4-6

web:
  host: 0.0.0.0
  port: 8080
  auth:
    enabled: true

users:
  - id: alice
    name: Alice
    password: changeme
    admin: true
  - id: bob
    name: Bob
    password: changeme2
    admin: false

telegram:
  enabled: false
  token: ""
  allowed_chat_ids: []

skills_dir: ./skills
```

---

## Two AI backends

### `claude-cli` (default)

Uses the official [Claude Code CLI](https://github.com/anthropics/claude-code) (`claude -p`).

- Install: `npm install -g @anthropic-ai/claude-code`
- Login: `claude` → follow the OAuth flow (Claude.ai subscription required)
- Free to use with an existing Claude.ai subscription
- **Anthropic-compliant**: uses the official CLI with your own account

### `anthropic-api`

Direct Anthropic API — requires an API key from [console.anthropic.com](https://console.anthropic.com).

- Supports native streaming
- ~$2–10/month for personal use depending on usage

---

## Skills

Create a folder under `skills/` with a `SKILL.md` file:

```
skills/
  my-skill/
    SKILL.md          ← describes what Claude can do with this skill
    my_script.py      ← optional helper script
```

Claude reads all `SKILL.md` files as part of its system prompt and picks the right skill based on what you ask. Two example skills are included:

- `skills/example/` — minimal starter template
- `skills/email-assistant/` — triage and reply to emails
- `skills/meta/` — manage your openNoClaw platform from the chat (create agents, skills, crons)

---

## Crons

Define automated tasks in `config.yml`:

```yaml
crons:
  - id: morning-brief
    name: "Morning brief"
    schedule: "0 7 * * 1-5"    # weekdays at 7am
    command: "claude -p 'Write a brief summary of today: weather, tasks, news' --allowedTools Bash"
    enabled: true
    notify:
      channels: [telegram]
      user: alice
```

The Automation panel shows last run status, next scheduled time, and lets you trigger runs manually.

---

## Connexions

Each user can configure their own integrations from Settings → Connexions:

| Integration | What it enables |
|-------------|-----------------|
| **Telegram** | Send/receive messages, run crons via Telegram |
| **Email (SMTP)** | Send emails, cron notifications |
| **Gmail** | Read and manage Gmail (OAuth) |
| **GitHub** | Create issues, merge PRs |
| **Linear** | Check boards, move tickets |
| **Notion** | Read/write databases |
| **Social** | Bluesky, Twitter/X, Reddit |

---

## Your keys, your instance

openNoClaw never ships with API keys or credentials. Each deployment uses its own:

- **Claude Code CLI backend** → your own Claude.ai subscription, authenticated via `claude` login
- **Anthropic API backend** → your own API key from [console.anthropic.com](https://console.anthropic.com)

This follows the same model as Nextcloud or Gitea: the code is shared, the data and credentials are yours.

**Fair use note:** Each person running their own separate instance should use their own subscription or API key. Sharing a single account across multiple households is against Anthropic's terms of service.

---

## Deploy on a VPS

```bash
# On your server
git clone https://github.com/gillesah/openNoClaw
cd openNoClaw

# Authenticate Claude Code CLI on the server
claude   # follow the login flow once

# Run
docker compose up -d
```

To expose it publicly, pair with [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) (no open ports needed):

```yaml
# /etc/cloudflared/config.yml
tunnel: your-tunnel-id
ingress:
  - hostname: myagent.example.com
    service: http://localhost:8080
  - service: http_status:404
```

---

## vs OpenClaw

| | OpenClaw | openNoClaw |
|-|----------|------------|
| AI backend | Unofficial Claude API | Official Claude Code CLI + Anthropic API |
| Web UI | No | Yes — for non-developers |
| Anthropic compliant | ⚠️ Unclear | ✅ Yes |
| Multi-user | No | Yes |
| Skills | Scripts | SKILL.md (natural language) |
| Crons | Yes | Yes + UI dashboard |
| Self-hosted | Yes | Yes |

---

## License

[MIT + Commons Clause](LICENSE) — free for personal and non-commercial use.
