#!/data/data/com.termux/files/usr/bin/bash
# Riavvia il sensore col codice aggiornato. In Termux:
#   cat ~/storage/shared/Download/run_agent.sh | bash
# Prima dell'avvio imposta (senza salvarli in una repo pubblica):
#   export POCKET_WIFI_COLLECTOR="http://<IP-tailscale-del-PC>:8099"
#   export POCKET_WIFI_TOKEN="un-token-lungo-e-casuale"

: "${POCKET_WIFI_COLLECTOR:?Imposta POCKET_WIFI_COLLECTOR}"
: "${POCKET_WIFI_TOKEN:?Imposta POCKET_WIFI_TOKEN}"

cp -f ~/storage/shared/Download/phone_agent.py ~/storage/shared/Download/bands.py ~/ 2>/dev/null \
  || cp -f /sdcard/Download/phone_agent.py /sdcard/Download/bands.py ~/ 2>/dev/null
cd ~
termux-wake-lock 2>/dev/null || true   # schermo spento: tieni sveglia la CPU
echo ">> avvio agente -> $POCKET_WIFI_COLLECTOR  (Ctrl-C per fermare)"
exec python phone_agent.py --collector "$POCKET_WIFI_COLLECTOR" \
  --token "$POCKET_WIFI_TOKEN" --interval "${POCKET_WIFI_INTERVAL:-15}"
