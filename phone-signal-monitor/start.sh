#!/usr/bin/env bash
# Avvia il collector (dashboard + statistiche) sul PC di casa.
# Configurazione:
#   export POCKET_WIFI_TOKEN='un-token-lungo-e-casuale'
#   ./start.sh
cd "$(dirname "$0")"

if [[ -z "${POCKET_WIFI_TOKEN:-}" ]]; then
  echo "ERRORE: imposta POCKET_WIFI_TOKEN prima di avviare il collector." >&2
  echo "Esempio: export POCKET_WIFI_TOKEN='un-token-lungo-e-casuale'" >&2
  exit 1
fi

exec python3 collector.py --token "$POCKET_WIFI_TOKEN"
