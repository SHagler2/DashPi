#!/usr/bin/env bash
# Runs on the bird station (BirdNET-Pi + AvianVisitors), not on DashPi.
# Renders the collage for each time window with AvianVisitors' frame/shoot.py
# and publishes the PNGs where Caddy serves them: http://<station>/dashpi/collage-<H>h.png,
# plus a dark copy of each (collage-<H>h-dark.png) for the plugin's Dark Mode setting.
set -u

AVIAN_DIR="${AVIAN_DIR:-$HOME/BirdNET-Pi}"
PYTHON="${PYTHON:-$HOME/dashpi-collage/.venv/bin/python}"
OUT_DIR="${OUT_DIR:-$HOME/BirdSongs/Extracted/dashpi}"
WIDTH="${WIDTH:-1024}"
HEIGHT="${HEIGHT:-600}"

HERE="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="${STATE_DIR:-$HERE/state}"

declare -A SUBTITLES=([1]="Last Hour" [12]="Last 12 Hours" [24]="Last 24 Hours" [168]="Last 7 Days")

mkdir -p "$OUT_DIR" "$STATE_DIR"
cd "$AVIAN_DIR/frame" || exit 1

failed=0
raw="$(mktemp --suffix=.png)"
# shoot.py forces the page's light theme and black bird names. The dark copy is made fresh
# each run, so it follows AvianVisitors updates; it must sit next to shoot.py, which finds
# its font relative to itself. Kept out of git status via .git/info/exclude.
dark_shoot="$AVIAN_DIR/frame/.shoot_dark.py"
trap 'rm -f "$raw" "$dark_shoot"' EXIT
grep -qxF 'frame/.shoot_dark.py' "$AVIAN_DIR/.git/info/exclude" 2>/dev/null \
    || echo 'frame/.shoot_dark.py' >> "$AVIAN_DIR/.git/info/exclude"
sed -e 's/"color_scheme": "light"/"color_scheme": "dark"/' \
    -e 's/\.gtile-label text { fill: #000 !important;/.gtile-label text { fill: #ece8e1 !important;/' \
    shoot.py > "$dark_shoot"
variants=(light)
if grep -q '"color_scheme": "dark"' "$dark_shoot" && grep -q 'fill: #ece8e1 !important' "$dark_shoot"; then
    variants+=(dark)
else
    echo "shoot.py changed; can't make the dark copy, rendering light only" >&2
    failed=1
fi

for variant in "${variants[@]}"; do
    suffix=""; shoot=shoot.py
    [[ "$variant" == dark ]] && { suffix="-dark"; shoot="$dark_shoot"; }
    for hours in 1 12 24 168; do
        final="$OUT_DIR/collage-${hours}h${suffix}.png"
        tmp="$OUT_DIR/.collage-${hours}h${suffix}.tmp.png"
        sig_file="$STATE_DIR/collage-${hours}h${suffix}.sig"

        # Skip the ~12 s render when the birds in this window haven't changed. The
        # image would come out identical, so just refresh its timestamp: DashPi reads
        # the file's age to tell whether the station is still working.
        sig="$("$PYTHON" "$HERE/signature.py" http://localhost "$hours" 2>/dev/null || true)"
        if [[ -n "$sig" && -f "$final" && "$(cat "$sig_file" 2>/dev/null)" == "$sig" ]]; then
            touch "$final"
            continue
        fi

        # 2x capture, then fit.py crops the empty paper and zooms the birds to fill the screen.
        # A wide, short cluster (xbias/ybias) matches the landscape screen best.
        if "$PYTHON" "$shoot" --url http://localhost --out "$raw" \
            --width "$WIDTH" --height "$HEIGHT" --dsf 2 --window-hours "$hours" \
            --title "Avian Visitors" --subtitle "${SUBTITLES[$hours]}" \
            --headline-px 30 --eyebrow-px 25 --mat 0.02 \
            --collage-vh 84 --cluster-xbias 1.8 --cluster-ybias 0.7 \
            --bird-names --timeout 90000 \
            && "$PYTHON" "$HERE/fit.py" "$raw" "$tmp" "$WIDTH" "$HEIGHT"; then
            chmod 644 "$tmp"
            mv -f "$tmp" "$final"   # atomic, so DashPi never downloads a half-written file
            [[ -n "$sig" ]] && echo "$sig" > "$sig_file"
            echo "rendered ${hours}h window ($variant)"
        else
            echo "render failed for ${hours}h window ($variant); keeping the previous image" >&2
            rm -f "$tmp"
            failed=1
        fi
    done
done
exit "$failed"
