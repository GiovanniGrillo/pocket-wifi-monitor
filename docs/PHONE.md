# Phone setup (Termux)

[Back to the README](../README.md)

How to run `phone_agent.py` on an Android phone with a SIM. Menu names below
are from MIUI/HyperOS (Xiaomi); other Android skins use similar ones.

## Requirements

- Termux and Termux:API, both from F-Droid (same source, so they can talk to
  each other).
- Tailscale, signed in to the same tailnet as the PC.
- Python and the `termux-api` package installed in Termux.
- The Phone and Location permissions granted to the Termux:API app.
- The phone on a charger for any long collection.

## Setup

Start the collector on the PC first, with a random token:

```bash
export POCKET_WIFI_TOKEN='random-token'
cd collector && ./start.sh
```

Copy `phone_agent.py`, `bands.py` and `run_agent.sh` to the phone's `Download`
folder, then in Termux:

```bash
pkg update && pkg install python termux-api
termux-setup-storage
export POCKET_WIFI_COLLECTOR='http://100.x.y.z:8099'
export POCKET_WIFI_TOKEN='random-token'
termux-wake-lock
bash ~/storage/shared/Download/run_agent.sh
```

Replace `100.x.y.z` with the address `tailscale ip -4` prints on the PC. Never
commit these values or write the real IP into the script.

## Check that it works

Termux prints one line per sample with band, RSRP, HTTP code and uplink state.
On the PC, open `http://127.0.0.1:8099`. Samples are written to
`collector/data/`, which stays local and is ignored by git.

If the signal fields are empty:

1. Check the Termux:API app is installed, not only the Termux package.
2. Check the Phone and Location permissions again.
3. Turn off battery optimization for both Termux and Termux:API.
4. Run `termux-telephony-cellinfo` by hand and see what it returns.
5. Some MIUI/Android versions simply return less. The agent keeps measuring
   HTTP connectivity either way.

## Signal gaps with the screen off

With the screen off and the phone in deep sleep (doze), MIUI/Android may return
nothing from `termux-telephony-cellinfo`. The modem stays connected and the HTTP
uplink keeps working, but the system does not report RSRP or the band at that
moment. In the "Signal over time" chart these samples appear as empty circles at
the bottom. They are not lost: the sample was delivered, only the signal number
is missing, and it cannot be recovered later because the system never produced
it.

To reduce the gaps, especially at night:

1. Give **Termux:API** location access **"Allow all the time"**, not "only while
   using the app". The cell API needs background location.
2. Set battery usage to **No restrictions** for **Termux:API** as well, not only
   for Termux.
3. On some MIUI/HyperOS versions, enable **Autostart** for Termux:API.
4. The wake-lock keeps the CPU awake but does not unlock location in doze on its
   own. The "Allow all the time" permission from step 1 is what matters.

This is not guaranteed: in deep sleep Android can still throttle the API, but it
usually recovers most of the night hours.

## Start after a reboot

With Termux:Boot installed and opened once:

```bash
mkdir -p ~/.termux/boot
cp ~/storage/shared/Download/boot-start-agent.sh ~/.termux/boot/start-agent.sh
chmod +x ~/.termux/boot/start-agent.sh
```

Put `POCKET_WIFI_COLLECTOR` and `POCKET_WIFI_TOKEN` in `~/.profile` (never in a
public repo). The boot script loads that file itself. The phone needs mobile
data and Tailscale running; the script waits a few seconds after boot before it
starts the agent.

## After a remote stop

A sensor stopped from the dashboard exits on the phone and cannot be restarted
remotely. Press *Resume sensor* on the dashboard to clear the stop, then run
`run_agent.sh` again in Termux.
