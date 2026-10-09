# Pocket-WiFi Monitor

Pocket-WiFi Monitor trasforma un telefono Android con SIM in un sensore remoto
per misurare la qualità della rete mobile. Il telefono legge segnale e cella
con Termux:API, esegue un piccolo controllo di connettività e invia i campioni
al computer di casa. Il computer li salva localmente e serve una dashboard web.

Il progetto è pensato come base riutilizzabile per altri sensori, non come
servizio cloud: non richiede database, account applicativi o pacchetti Python
esterni.

> **Stato:** prototipo funzionante. Le soglie radio sono euristiche e i dati
> raccolti dipendono dal modem, dalla versione Android e dall'operatore.

## Architettura

```text
Telefono Android                         PC di raccolta
Termux + Termux:API                      collector.py
phone_agent.py ── HTTP su Tailscale ──▶  data/ + dashboard
```

- `phone_agent.py` legge LTE, batteria e connettività, mantiene un outbox locale
  e ritenta i campioni quando il PC non è raggiungibile.
- `collector.py` riceve singoli campioni o batch, deduplica nella vista storica
  e serve `/`, `/stats`, `/data`, `/history` e `/control` (stop remoto e stress test).
- `bands.py` contiene solo funzioni standard-library per EARFCN, bande e qualità.
- `adb_signal.py` offre una lettura rapida via USB/ADB senza Termux.
- `stresstest.py` misura throughput e latenza sotto carico tramite tethering USB.

## Sicurezza e dati

Questa repo è progettata per essere pubblica, ma i campioni reali **non** lo
sono: possono contenere operatore, identificativi di cella, tempi e stato della
batteria. La directory `data/` è ignorata da Git e contiene solo `.gitkeep`.

Il token è passato nell'header `X-Token`; non inserire token, IP Tailscale,
operatori o log reali negli script, nelle issue o nei commit. Generane uno
casuale, per esempio:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Il collector rifiuta l'avvio esposto sulla rete senza `--token` e protegge sia
le API di scrittura sia quelle di lettura. Tailscale è consigliato per evitare
il port forwarding; non è una sostituzione del token.

Il collector parla **HTTP in chiaro**: la riservatezza del token e dei dati in
transito dipende dalla cifratura di Tailscale (WireGuard). Non esporre il
collector su un'interfaccia pubblica senza un reverse proxy TLS davanti. Per
difesa in profondità puoi legarlo al solo indirizzo Tailscale invece che a
`0.0.0.0`:

```bash
python3 collector.py --bind "$(tailscale ip -4 | head -1)" --token "$POCKET_WIFI_TOKEN"
```

## Avvio sul PC

Requisiti: Python 3.9+ e Tailscale. Il codice usa solo la libreria standard.

```bash
cd phone-signal-monitor
export POCKET_WIFI_TOKEN='inserisci-qui-un-token-casuale'
./start.sh
```

Apri `http://127.0.0.1:8099`. Per una configurazione manuale:

```bash
python3 collector.py --bind 0.0.0.0 --port 8099 \
  --token "$POCKET_WIFI_TOKEN"
```

L'indirizzo Tailscale del PC si trova con `tailscale ip -4`.

## Installazione sul telefono (Termux)

Questa procedura è adatta anche a un vecchio Xiaomi con Android 5G; i nomi dei
menu possono cambiare a seconda della MIUI/HyperOS.

1. Installa **Termux** e **Termux:API** da F-Droid. Le due app devono provenire
   dalla stessa fonte/versione.
2. Installa Tailscale sul telefono, accedi allo stesso tailnet del PC e verifica
   che il PC risponda al suo IP `100.x.y.z`.
3. In Termux esegui:

   ```bash
   pkg update
   pkg install python termux-api
   termux-setup-storage
   ```

4. Concedi a Termux:API i permessi Telefono e Posizione. Android può revocarli
   dopo periodi di inattività: controllali se i campi radio risultano vuoti.
5. Copia `phone_agent.py`, `bands.py` e `run_agent.sh` nella cartella
   `Download` condivisa del telefono.
6. In Termux, **non mettere i valori sensibili nel file copiato**:

   ```bash
   export POCKET_WIFI_COLLECTOR='http://100.x.y.z:8099'
   export POCKET_WIFI_TOKEN='lo-stesso-token-del-PC'
   bash ~/storage/shared/Download/run_agent.sh
   ```

   Lascia il telefono sotto carica e usa `termux-wake-lock` per evitare la
   sospensione durante il test. `--interval` predefinito è 15 secondi nello
   script di avvio; aumentalo se vuoi ridurre consumo e traffico.

### Avvio dopo il riavvio

Installa l'app **Termux:Boot**, aprila una volta, poi:

```bash
mkdir -p ~/.termux/boot
cp ~/storage/shared/Download/boot-start-agent.sh ~/.termux/boot/start-agent.sh
chmod +x ~/.termux/boot/start-agent.sh
```

Salva le due variabili `POCKET_WIFI_COLLECTOR` e `POCKET_WIFI_TOKEN` in
`~/.profile` (con permessi limitati) prima di usare Termux:Boot. Disabilita
l'ottimizzazione batteria per Termux, Termux:API e Tailscale se il telefono
interrompe il processo in background.

## Interpretare i risultati

- RSRP: circa `>= -90 dBm` buono, `-90/-100` discreto, `-100/-110` scarso,
  sotto `-110` critico.
- SINR positivo e alto indica una qualità migliore; vicino a zero o negativo
  può ridurre la velocità anche con RSRP accettabile.
- Una banda sub-GHz (per esempio B20 800 MHz) tende a propagarsi meglio, ma non
  garantisce da sola prestazioni elevate.
- `http 204` misura che il traffico funziona, non la velocità disponibile.
  Per la velocità usa lo stress test con un budget dati esplicito.

## Controllo remoto e sicurezza del sensore

Dalla dashboard (o via `POST /control`) puoi, anche da fuori casa:

- **Fermare/riprendere** il sensore: alza un flag che il telefono legge al ciclo
  successivo e termina in modo pulito. Utile per interrompere da remoto.
- **Avviare uno stress test** on-demand sulla rete mobile del telefono (download
  a più flussi + latenza sotto carico), con un **budget dati** scelto al momento;
  il risultato torna nella dashboard. Misura la rete reale del posto, non il
  tethering.

L'agente ha inoltre un **cutoff di sicurezza automatico**: se la temperatura
della batteria supera una soglia (`--max-temp`, default 44 °C) per alcune letture
consecutive, si ferma da solo — anche senza PC raggiungibile. È una prudenza in
più: il telefono mantiene comunque la propria protezione termica hardware.

## Test opzionali

Segnale via USB/ADB:

```bash
python3 adb_signal.py --loop --interval 5 --csv
```

Throughput e bufferbloat via tethering USB (il default può usare fino a 300 MB;
il tetto `--budget-mb` è approssimato alla granularità del blocco scaricato):

```bash
python3 stresstest.py --budget-mb 100 --phase 20
```

Controlla il budget del tuo piano e interrompi con `Ctrl-C` quando necessario.

## Sviluppare e riusare il progetto

Per aggiungere un nuovo sensore, mantieni il contratto di un campione JSON:
`timestamp`, `source`, eventuali metriche e `http_ok`. Il collector accetta
campi aggiuntivi senza migrazioni; la dashboard usa quelli che riconosce.

Percorso consigliato:

1. Estrai la lettura hardware in un modulo senza rete.
2. Riusa `bands.py` o aggiungi un parser specifico con test di esempio
   anonimizzati.
3. Mantieni il trasporto separato dalla raccolta: l'outbox deve continuare a
   funzionare anche senza connessione.
4. Aggiungi test per parser e soglie prima di modificare la dashboard.
5. Non includere mai dati reali nei fixture: sostituisci operatori, ID e
   timestamp con valori sintetici.

## Pubblicazione consigliata

La cronologia dovrebbe essere composta da piccoli commit indipendenti:

1. `chore: add public repository hygiene`
2. `refactor: move runtime configuration to environment`
3. `fix: protect collector read APIs`
4. `docs: document Termux and reproducible setup`
5. `test: add parser and signal classification coverage`

Prima del primo push esegui `git status`, controlla `git diff --cached` e verifica
che `data/` e i log non siano inclusi. Pubblica solo codice, documentazione,
esempi redatti e test sintetici.

## Licenza

Distribuito sotto licenza **MIT**: vedi [`LICENSE`](LICENSE). In breve: puoi
usare, modificare e ridistribuire il codice, senza garanzie.
