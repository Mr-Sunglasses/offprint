#!/bin/sh
# Seed the writable site table on first boot so auto-update can replace it.
set -eu

if [ -n "${OFFPRINT_SITES_JS:-}" ]; then
  mkdir -p "$(dirname "${OFFPRINT_SITES_JS}")"
  if [ ! -f "${OFFPRINT_SITES_JS}" ]; then
    cp /app/src/offprint/data/sites.js "${OFFPRINT_SITES_JS}"
    echo "seeded site table at ${OFFPRINT_SITES_JS}"
  fi
fi

exec "$@"
