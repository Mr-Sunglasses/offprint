#!/bin/sh
# Standalone Chromium with a CDP endpoint other containers can reach.
#
# Recent Chromium builds bind the DevTools socket to 127.0.0.1 even when asked
# for 0.0.0.0, so we run Chromium on loopback and expose it through a tiny
# Python TCP proxy. No extra packages, no socat.
set -eu

LISTEN_PORT="${BPC_CDP_PORT:-9222}"
INNER_PORT=$((LISTEN_PORT + 1))

CHROME="$(ls -d /ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | head -1)"
if [ -z "${CHROME}" ]; then
  CHROME="$(ls -d /ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell 2>/dev/null | head -1)"
fi
if [ -z "${CHROME}" ]; then
  echo "chromium not found under /ms-playwright" >&2
  exit 1
fi

echo "starting chromium (${CHROME}) on 127.0.0.1:${INNER_PORT}, proxied to 0.0.0.0:${LISTEN_PORT}"
"${CHROME}" \
  --headless=new \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --hide-scrollbars \
  --mute-audio \
  --disable-blink-features=AutomationControlled \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="${INNER_PORT}" \
  --remote-allow-origins='*' \
  about:blank &

CHROME_PID=$!

# wait for the DevTools endpoint before opening the proxy
i=0
while [ $i -lt 40 ]; do
  if python3 - <<PY 2>/dev/null
import socket
socket.create_connection(("127.0.0.1", ${INNER_PORT}), timeout=0.5).close()
PY
  then break; fi
  i=$((i + 1))
  sleep 0.5
done

exec python3 /usr/local/bin/cdp_proxy.py "${LISTEN_PORT}" "${INNER_PORT}"
