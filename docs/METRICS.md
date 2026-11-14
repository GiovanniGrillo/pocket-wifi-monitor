# Reading the numbers

[Back to the README](../README.md)

## The three pages

- **Overview** (`/`): the live view. Four KPIs (typical signal, usable time,
  data reliability, data used), the signal over time on a real time axis so
  gaps stay visible, the quality mix, realtime cards for signal, network,
  connectivity and device, and the stress test. The sensor stop/resume control
  sits in the top bar.
- **Analysis** (`/stats`): the verdict for a usage profile, the signal meter,
  day-by-day and hour-of-day patterns, sensor uptime, conclusions, an estimate
  of what a 4G/5G router would gain, and a short glossary.
- **Data** (`/explore`): a sortable, filterable, paginated table of the samples
  in the chosen window (24 h, 7 d, 30 d, all), with CSV export.

Every page is a single HTML file with hand-drawn SVG charts, in English and
Italian, with a light and a dark theme.

## Signal scale

The phone, the Overview and the Analysis page all use the same RSRP scale:

| RSRP | Quality |
| --- | --- |
| ≥ -80 dBm | excellent |
| -80 to -90 | good |
| -90 to -100 | fair |
| -100 to -110 | poor |
| below -110 | very poor |

A low SINR (below 5 dB, or negative) pulls the verdict down even when RSRP is
acceptable. Many phones, MIUI included, do not report SINR at all.

## Things to keep in mind

- A sub-GHz band (B20 / 800 MHz) reaches further and goes through walls better,
  but does not guarantee high throughput on its own.
- `HTTP 204` proves that traffic passes, not how fast. Use the stress test with
  an explicit budget to measure throughput.
- Signal gaps at night are usually a reporting gap, not lost data: in deep
  sleep with the screen off Android may stop returning cell info while uploads
  keep working. See [PHONE.md](PHONE.md).
- The thresholds are heuristics. What you can read depends on the modem, the
  Android version and the carrier.
