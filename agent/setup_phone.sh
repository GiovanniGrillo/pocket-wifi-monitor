#!/data/data/com.termux/files/usr/bin/bash
# Setup + avvio del sensore telefono, da eseguire in Termux.
#   1) in Termux:  termux-setup-storage      (tocca "Consenti")
#   2) in Termux:  bash /sdcard/Download/setup_phone.sh
#
# Prima dell'avvio imposta nel terminale Termux:
#   export POCKET_WIFI_COLLECTOR="http://<IP-tailscale-del-PC>:8099"
#   export POCKET_WIFI_TOKEN="un-token-lungo-e-casuale"

: "${POCKET_WIFI_COLLECTOR:?Imposta POCKET_WIFI_COLLECTOR}"
: "${POCKET_WIFI_TOKEN:?Imposta POCKET_WIFI_TOKEN}"

echo ">> [1/4] Installo python e termux-api..."
pkg update -y && pkg install -y python termux-api || {
  echo "   installazione pacchetti fallita (rete attiva?)"; exit 1; }

echo ">> [2/4] Accesso allo storage (se chiede, tocca Consenti)..."
termux-setup-storage || true

echo ">> [3/4] Copio gli script nella home di Termux..."
cp -f /sdcard/Download/phone_agent.py /sdcard/Download/bands.py "$HOME"/ 2>/dev/null \
  || cp -f "$HOME"/storage/shared/Download/phone_agent.py \
           "$HOME"/storage/shared/Download/bands.py "$HOME"/ 2>/dev/null \
  || { echo "   NON trovo gli script in Download — dimmelo"; exit 1; }
cd "$HOME"

echo ">> [4/4] Test lettura cella (se vuoto/errore, COPIAMI questo output):"
echo "----------------------------------------------------------------"
termux-telephony-cellinfo \
  || echo "termux-telephony-cellinfo NON disponibile — Termux:API da F-Droid e permessi Telefono/Posizione?"
echo "----------------------------------------------------------------"
echo

echo ">> Avvio agente -> $POCKET_WIFI_COLLECTOR   (Ctrl-C per fermare)"
exec python phone_agent.py --collector "$POCKET_WIFI_COLLECTOR" \
  --token "$POCKET_WIFI_TOKEN" --interval "${POCKET_WIFI_INTERVAL:-15}"
