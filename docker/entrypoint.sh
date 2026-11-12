#!/bin/sh
# Seed the writable site table on first boot so auto-update can replace it.
set -eu

if [ -n "${BPC_SITES_JS:-}" ]; then
  mkdir -p "$(dirname "${BPC_SITES_JS}")"
  if [ ! -f "${BPC_SITES_JS}" ]; then
    cp /app/src/bpc_web/data/sites.js "${BPC_SITES_JS}"
    echo "seeded site table at ${BPC_SITES_JS}"
  fi
fi

exec "$@"
