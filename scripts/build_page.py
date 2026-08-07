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
TEMPLATE = ROOT / "page" / "template.html"
REPORT = ROOT / "results" / "report.json"
OUT = ROOT / "results" / "index.html"


def main() -> int:
    if not REPORT.exists():
        sys.exit("error: results/report.json missing — run scripts/export_report.py first")

    data = json.loads(REPORT.read_text())
    html = TEMPLATE.read_text(encoding="utf-8")

    if "__DATA__" not in html:
        sys.exit("error: template has no __DATA__ placeholder")

    # Compact JSON keeps the page small; </script> inside a string would end the
    # block early, so escape the only sequence that can do that.
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("__DATA__", payload)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
