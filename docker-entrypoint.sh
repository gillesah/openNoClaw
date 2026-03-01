#!/bin/bash
# Fix .claude directory ownership if mounted as root (requires CHOWN capability)
if [ -d /home/node/.claude ] && [ "$(stat -c %u /home/node/.claude)" != "1000" ]; then
  chown -R node:node /home/node/.claude 2>/dev/null || true
fi
# Fix /data directory ownership if mounted as root
if [ -d /data ] && [ "$(stat -c %u /data)" != "1000" ]; then
  chown -R node:node /data 2>/dev/null || true
fi
# Start Xvfb for non-headless browser automation (Playwright)
Xvfb :99 -screen 0 1280x800x24 -ac &
export DISPLAY=:99
exec "$@"
