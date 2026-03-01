"""
Example skill — demonstrates how skill Python scripts can be structured.
Claude can invoke this via bash tool if configured.
"""

import sys
import json
from datetime import datetime


def cmd_time():
    """Return current time as JSON."""
    return {"time": datetime.now().isoformat(), "timezone": "local"}


def cmd_echo(text: str):
    """Echo back a message."""
    return {"echo": text}


COMMANDS = {
    "time": lambda: cmd_time(),
    "echo": lambda: cmd_echo(" ".join(sys.argv[2:])) if len(sys.argv) > 2 else cmd_echo(""),
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd in COMMANDS:
        print(json.dumps(COMMANDS[cmd](), indent=2))
    else:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS)}")
        sys.exit(1)
