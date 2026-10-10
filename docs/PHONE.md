# Guida telefono / Termux

Questa guida è il riferimento operativo per eseguire `phone_agent.py` su un
telefono Android con SIM. Per il flusso completo e le decisioni di sicurezza
vedi [README.md](README.md).

## Requisiti

- Termux e Termux:API installati da F-Droid;
- Tailscale installato e autenticato sullo stesso tailnet del PC;
- Python e `termux-api` installati in Termux;
- permessi Telefono e Posizione concessi a Termux:API;
- telefono collegato all'alimentazione durante una raccolta lunga.

## Configurazione

Sul PC avvia prima il collector con un token casuale:

```bash
export POCKET_WIFI_TOKEN='token-casuale'
cd collector && ./start.sh
```

Sul telefono copia `phone_agent.py`, `bands.py` e `run_agent.sh` in
`Download`, poi in Termux:

```bash
pkg update && pkg install python termux-api
termux-setup-storage
export POCKET_WIFI_COLLECTOR='http://100.x.y.z:8099'
export POCKET_WIFI_TOKEN='token-casuale'
termux-wake-lock
bash ~/storage/shared/Download/run_agent.sh
```

Sostituisci `100.x.y.z` con l'IP mostrato da `tailscale ip -4` sul PC. Non
committare mai questi valori e non inserire l'IP reale nello script.

## Verifica

Sul telefono dovresti vedere righe con banda, RSRP, codice HTTP e stato uplink.
Sul PC apri `http://127.0.0.1:8099`; i dati vengono scritti in `data/`, che è
locale e ignorata dal repository.

Se il segnale è vuoto:

1. verifica l'app Termux:API, non solo il pacchetto Python;
2. ricontrolla permessi Telefono/Posizione;
3. disabilita l'ottimizzazione batteria per Termux e Termux:API;
4. prova `termux-telephony-cellinfo` manualmente;
5. considera le differenze MIUI/Android: l'agente continua comunque a misurare
   la connettività HTTP.

## Buchi di segnale a schermo spento

Su MIUI/Android, con lo schermo spento e il telefono in sospensione profonda
(*doze*), `termux-telephony-cellinfo` può restituire vuoto: il modem resta
connesso e l'uplink HTTP continua a funzionare, ma il valore di RSRP/banda non
viene fornito dal sistema in quel momento. Nel grafico "Signal over time" questi
campioni appaiono come cerchietti vuoti sul fondo, non come dati persi: lo
store-and-forward li ha inviati, manca solo il numero del segnale, e non è
recuperabile a posteriori perché il sistema non l'ha mai prodotto.

Per ridurre i buchi (soprattutto di notte):
## Avvio automatico

Con Termux:Boot:

```bash
mkdir -p ~/.termux/boot
cp ~/storage/shared/Download/boot-start-agent.sh ~/.termux/boot/start-agent.sh
chmod +x ~/.termux/boot/start-agent.sh
```

Metti `POCKET_WIFI_COLLECTOR` e `POCKET_WIFI_TOKEN` in `~/.profile`, non in una
repo pubblica. Il telefono deve essere acceso, avere rete mobile e Tailscale
attivo; lo script aspetta alcuni secondi dopo il boot.
