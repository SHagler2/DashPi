# BirdNet Collage

Plugin id: `avian_collage`

Shows the live bird collage from a BirdNET-Pi station running
[AvianVisitors](https://github.com/Twarner491/AvianVisitors) full-screen on DashPi,
for the last hour, 12 hours, 24 hours or week.

## Requirements

This plugin does not work on its own. It needs:

- **A separate Raspberry Pi running [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi)
  with the [AvianVisitors](https://github.com/Twarner491/AvianVisitors) overlay** and a
  microphone. Tested on a Pi 4B; it needs enough memory to run headless Chromium, so not a Pi Zero.
- **The render timer from `station/` installed on that Pi** (see [Station setup](#station-setup)).
  It turns the collage into the PNGs this plugin downloads.
- **Both Pis on the same network.** DashPi reaches the station at `http://birdnet.local` by default.

Without the station, the plugin shows "Bird station unreachable"; without the render
timer, it shows "Collage image not found".

## How it works

The collage is a JavaScript page that takes a Pi Zero 2 W 70-120 s (and far more
memory than DashPi can spare) to render in headless Chromium. So the **bird station
renders it** and DashPi only downloads the finished PNG:

```
bird station (Pi 4B)                                  DashPi
  dashpi-collage.timer, every 5 min
  └─ station/render.sh, for 1h / 12h / 24h / 168h
     ├─ station/signature.py          (skip the window if its birds haven't changed)
     ├─ AvianVisitors frame/shoot.py  (headless Chromium, 2x capture)
     └─ station/fit.py                (crop empty paper, zoom birds to 1024x600)
        └─▶ ~/BirdSongs/Extracted/dashpi/collage-<H>h.png ──HTTP──▶ avian_collage plugin
```

`shoot.py` is AvianVisitors' own capture tool for its e-ink frame. It hides the menu
button and other controls and waits for the collage to finish loading. It runs
unmodified from the station's AvianVisitors checkout. DashPi contains none of its code.

The page lays the birds out for a portrait frame, which leaves a landscape screen
mostly empty, so `fit.py` keeps the title at its rendered size and scales the bird
cluster up to fill the rest.

## How fresh it is

Every 5 minutes the station checks each window's species and call-count brackets
(the same idea as AvianVisitors' e-ink frame) and re-renders only the windows that
changed. Every window is re-rendered at least hourly. On a Pi 4B a full render of all three
windows takes about 35 s, and a check that finds no change takes about 3 s.
DashPi downloads the current PNG every time it shows the plugin, so the screen is
at most about 5 minutes behind the station.

## Settings

| Setting | Default | |
|---|---|---|
| Bird station URL | `http://birdnet.local` | The station running AvianVisitors and the render timer |
| Time window | Last 24 hours | Last hour, last 12 hours, last 24 hours or last week |
| Auto Refresh | Every 15 minutes | Re-fetch while displayed or pinned outside a loop; on e-ink the panel only redraws when the image changed |
| Display Mode | Fill | Fill crops to the screen; Fit letterboxes |

## Errors shown in the web UI

- **Bird station unreachable at …**: the station is offline or the hostname doesn't resolve.
- **Collage image not found … Is the render timer set up?**: the station is up but hasn't written the PNG.
- A PNG older than 2 hours is still shown, and a warning is logged.

## Station setup

On the bird station (tested on a Pi 4B running Debian 13, with AvianVisitors cloned at
`~/BirdNET-Pi` and the user `admin`; edit the paths in `render.sh` and `User=` in the
service otherwise). Copy this folder's `station/` files to `~/dashpi-collage/`, then:

```bash
cd ~/dashpi-collage
python3 -m venv .venv
.venv/bin/pip install -r ~/BirdNET-Pi/frame/requirements-shoot.txt
.venv/bin/playwright install chromium
# Install the libraries Playwright reports as missing, for example on Debian 13:
sudo apt-get install -y libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libxcomposite1 libxdamage1 libatspi2.0-0t64

chmod +x render.sh fit.py
sudo install -m 644 dashpi-collage.service dashpi-collage.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dashpi-collage.timer
sudo systemctl start dashpi-collage.service     # first render now
```

Check it with `curl -I http://birdnet.local/dashpi/collage-24h.png` and
`journalctl -u dashpi-collage`. Caddy already serves `~/BirdSongs/Extracted`, so no
web server changes are needed. If a render fails, the previous PNG stays in place.

## Credit and license

The collage, its illustrations and `shoot.py` are from
[AvianVisitors](https://github.com/Twarner491/AvianVisitors) by Twarner491, a fork of
[BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi). They are licensed
**CC-BY-NC-SA-4.0** (non-commercial). This plugin only fetches the rendered image over
HTTP, and the `station/` scripts call `shoot.py` without copying it. DashPi contains no
AvianVisitors code or artwork.
