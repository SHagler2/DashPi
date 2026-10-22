#!/usr/bin/env python3
"""Print a short signature of the birds in a time window, so render.sh can skip unchanged windows.

The signature covers which species were heard and a coarse bracket of each one's
call count (the collage sizes birds by count), so a new species or a bird getting
much louder triggers a re-render, but one more call from a frequent visitor doesn't.

    signature.py <station_url> <hours>
"""
import hashlib
import json
import sys
import urllib.request

COUNT_BRACKETS = (1, 2, 5, 15, 40, 100, 300, 1000)


def bracket(n):
    return next((i for i, edge in enumerate(COUNT_BRACKETS) if n <= edge), len(COUNT_BRACKETS))


def main(station_url, hours):
    url = f"{station_url.rstrip('/')}/avian/api/birdnet-api.php?action=recent&hours={int(hours)}"
    with urllib.request.urlopen(url, timeout=20) as response:
        species = json.loads(response.read(2_000_000))["species"]
    items = sorted((s["sci"], bracket(int(s.get("n") or 1))) for s in species)
    print(hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
