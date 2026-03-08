# Base: Node 22 slim (needed for Claude Code CLI)
FROM node:22-slim

# Install Python 3 + pip + Xvfb (for non-headless Playwright)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv curl xvfb docker.io ffmpeg \
  && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Security: non-root user (node user already exists in node image)
# We reuse the existing 'node' user (uid 1000)
WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Install Playwright Chromium in a system-wide path (readable by node user)
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/local/ms-playwright
RUN python3 -m playwright install chromium --with-deps && \
    chmod -R a+rX /usr/local/ms-playwright

# App code
COPY core/ ./core/
COPY interfaces/ ./interfaces/
COPY main.py .

# Persistent dirs + X11 socket dir for Xvfb (must be created as root)
RUN mkdir -p /data /skills /home/node/.claude /tmp/.X11-unix && \
    chmod 1777 /tmp/.X11-unix && \
    chown -R node:node /app /data /skills /home/node/.claude

# Entrypoint: fix ownership + start Xvfb at startup
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

USER node
ENV HOME=/home/node
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/local/ms-playwright

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/skills')" || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python3", "main.py"]
