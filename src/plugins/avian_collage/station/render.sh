#!/usr/bin/env bash
# Runs on the bird station (BirdNET-Pi + AvianVisitors), not on DashPi.
# Renders the collage for each time window with AvianVisitors' frame/shoot.py
# and publishes the PNGs where Caddy serves them: http://<station>/dashpi/collage-<H>h.png
set -u

AVIAN_DIR="${AVIAN_DIR:-$HOME/BirdNET-Pi}"
PYTHON="${PYTHON:-$HOME/dashpi-collage/.venv/bin/python}"
OUT_DIR="${OUT_DIR:-$HOME/BirdSongs/Extracted/dashpi}"
WIDTH="${WIDTH:-1024}"
HEIGHT="${HEIGHT:-600}"

HERE="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="${STATE_DIR:-$HERE/state}"

declare -A SUBTITLES=([12]="Last 12 Hours" [24]="Last 24 Hours" [168]="Last 7 Days")

mkdir -p "$OUT_DIR" "$STATE_DIR"
cd "$AVIAN_DIR/frame" || exit 1

failed=0
raw="$(mktemp --suffix=.png)"
trap 'rm -f "$raw"' EXIT

for hours in 12 24 168; do
    final="$OUT_DIR/collage-${hours}h.png"
    tmp="$OUT_DIR/.collage-${hours}h.tmp.png"
    sig_file="$STATE_DIR/collage-${hours}h.sig"

    # Skip the ~12 s render when the birds in this window haven't changed, but
    # re-render at least hourly so the image's age never looks stale to DashPi.
    sig="$("$PYTHON" "$HERE/signature.py" http://localhost "$hours" 2>/dev/null || true)"
    if [[ -n "$sig" && -f "$final" && "$(cat "$sig_file" 2>/dev/null)" == "$sig" \
          && -z "$(find "$final" -mmin +60)" ]]; then
        continue
    fi

    # 2x capture, then fit.py crops the empty paper and zooms the birds to fill the screen.
    # A wide, short cluster (xbias/ybias) matches the landscape screen best.
    if "$PYTHON" shoot.py --url http://localhost --out "$raw" \
        --width "$WIDTH" --height "$HEIGHT" --dsf 2 --window-hours "$hours" \
        --title "Avian Visitors" --subtitle "${SUBTITLES[$hours]}" \
        --headline-px 30 --eyebrow-px 15 --mat 0.02 \
        --collage-vh 84 --cluster-xbias 1.8 --cluster-ybias 0.7 \
        --timeout 90000 \
        && "$PYTHON" "$HERE/fit.py" "$raw" "$tmp" "$WIDTH" "$HEIGHT"; then
        chmod 644 "$tmp"
        mv -f "$tmp" "$final"   # atomic, so DashPi never downloads a half-written file
        [[ -n "$sig" ]] && echo "$sig" > "$sig_file"
        echo "rendered ${hours}h window"
    else
        echo "render failed for ${hours}h window; keeping the previous image" >&2
        rm -f "$tmp"
        failed=1
    fi
done
exit "$failed"
