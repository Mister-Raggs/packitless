"""Build the results page by injecting measured data into the template.

Every number on the page comes from results/report.json, so nothing is
hand-copied and the page regenerates from measurements after any change.

Usage:
    python scripts/export_report.py --exact && python scripts/build_page.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "results" / "report.json"
# Both artefacts are built from the same measurements, so the deck can never
# quote a figure the results page contradicts.
# docs/ is served by GitHub Pages straight off the default branch: a public
# URL that needs no login, no hosting account, and no deploy step beyond the
# push that was happening anyway.
TARGETS = [
    (ROOT / "page" / "template.html", ROOT / "results" / "index.html"),
    (ROOT / "page" / "slides.html", ROOT / "results" / "slides.html"),
    (ROOT / "page" / "template.html", ROOT / "docs" / "index.html"),
    (ROOT / "page" / "slides.html", ROOT / "docs" / "slides.html"),
]


def main() -> int:
    if not REPORT.exists():
        sys.exit("error: results/report.json missing — run scripts/export_report.py first")

    data = json.loads(REPORT.read_text())
    # Compact JSON keeps the pages small; </script> inside a string would end
    # the block early, so escape the only sequence that can do that.
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")

    for template, out in TARGETS:
        if not template.exists():
            print(f"skip {template.name}: not found")
            continue
        html = template.read_text(encoding="utf-8")
        if "__DATA__" not in html:
            sys.exit(f"error: {template.name} has no __DATA__ placeholder")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html.replace("__DATA__", payload), encoding="utf-8")
        print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
