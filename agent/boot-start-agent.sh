#!/data/data/com.termux/files/usr/bin/sh
# Termux:Boot — riavvia l'agente dopo il riavvio del telefono.
# Installazione (una volta, in Termux):
#   mkdir -p ~/.termux/boot
#   cp ~/storage/shared/Download/boot-start-agent.sh ~/.termux/boot/start-agent.sh
#   chmod +x ~/.termux/boot/start-agent.sh
# (serve anche l'app "Termux:Boot" da F-Droid, aperta almeno una volta)

termux-wake-lock
sleep 15                      # lascia salire rete + Tailscale dopo il boot
cd "$HOME"
# Store private values in ~/.profile or export them before installing this hook.
: "${POCKET_WIFI_COLLECTOR:?Imposta POCKET_WIFI_COLLECTOR in ~/.profile}"
: "${POCKET_WIFI_TOKEN:?Imposta POCKET_WIFI_TOKEN in ~/.profile}"
exec bash ~/storage/shared/Download/run_agent.sh >> "$HOME/agent.log" 2>&1
