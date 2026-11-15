"""Build the two self-contained course deliverables from their editable sources.

    python3 scripts/build_deliverables.py

report/report_src.html  ->  report.html   (the written report, prints to US Letter)
report/slides_src.html  ->  slides.html   (reveal.js deck, 10 slides)

Every <img src="img/..."> is inlined as a base64 data URI and <!--DIAGRAM--> is replaced
with report/diagram.svg, so each output opens on any computer with no other files.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "report"


def inline(html: str) -> str:
    def data_uri(m: re.Match) -> str:
        path = SRC / m.group(1)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f'src="data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"'

    html = re.sub(r'src="(img/[^"]+)"', data_uri, html)
    svg = (SRC / "diagram.svg").read_text()
    html = html.replace("<!--DIAGRAM-->", svg)
    leftover = re.findall(r'src="(?!data:|https://)[^"]+"', html)
    if leftover:
        raise SystemExit(f"Not inlined: {leftover}")
    return html


def main() -> None:
    for src, out in (("report_src.html", "report.html"), ("slides_src.html", "slides.html")):
        html = inline((SRC / src).read_text())
        (ROOT / out).write_text(html)
        print(f"{out:12} {len(html) / 1024:,.0f} KB")


if __name__ == "__main__":
    main()
