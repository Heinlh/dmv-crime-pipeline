"""Render the daily Open Graph share card as a PNG.

Reads site/data/digest.json (written by export_site_data) and draws a
1200x630 card in the site's visual language: dark ground, tri-color DMV
mark, the latest data day's headline numbers, and category bars. The
card is pure data presentation; no narrative text, so nothing here can
say more than the agencies published.

Rendered entirely in Python (SVG composed by hand, rasterized with
cairosvg) so the pipeline needs no headless browser. Fonts fall back to
whatever sans-serif the runner has; the layout does not depend on a
specific typeface. Output lands in site/og/, which deploys with the
site and is referenced by absolute og:image URLs in the page heads.
"""

import json
import logging
from xml.sax.saxutils import escape

import cairosvg

from config import PROJECT_ROOT, SITE_DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s og: %(message)s")
logger = logging.getLogger(__name__)

OG_DIR = PROJECT_ROOT / "site" / "og"
W, H = 1200, 630

# Mirrors the site's design tokens (site/css/style.css :root) and the
# category colors in site/js/common.js CATEGORIES.
BG = "#0a0a14"
SURFACE = "#10101d"
BORDER = "#232138"
GRID = "#1d1b30"
TEXT = "#eaf0fb"
TEXT_2 = "#a6b0c9"
MUTED = "#7b85a1"
ACCENT = "#4de3ff"
ACCENT_2 = "#d17bff"
WARN = "#ffb454"
CATEGORY_COLORS = {
    "homicide": "#cd3a3a",
    "violent": "#bf4f00",
    "sexual": "#aa6ad1",
    "property": "#0072B2",
    "vehicle": "#009E73",
    "disorder": "#c98500",
    "other": "#8A8A8A",
}
CATEGORY_LABELS = {
    "homicide": "Homicide",
    "violent": "Violent",
    "sexual": "Sexual",
    "property": "Property",
    "vehicle": "Vehicle",
    "disorder": "Disorder",
    "other": "Other",
}
JURISDICTION_LABELS = {
    "dc": "Washington DC",
    "moco": "Montgomery Co",
    "pgc": "Prince George's Co",
    "fairfax": "Fairfax Co",
}
FONT = "'DejaVu Sans', 'Helvetica Neue', Arial, sans-serif"


def _fmt_day(iso_day: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso_day)
    return d.strftime("%A, %B %d, %Y").replace(" 0", " ")


def build_svg(digest: dict) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        f'<rect width="{W}" height="{H}" fill="{BG}"/>',
    ]
    # faint grid, same rhythm as the site background
    for x in range(0, W + 1, 60):
        parts.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{H}" stroke="{GRID}" stroke-width="1" opacity="0.5"/>')
    for y in range(0, H + 1, 60):
        parts.append(f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="{GRID}" stroke-width="1" opacity="0.5"/>')
    # top accent rule
    parts.append(f'<rect x="0" y="0" width="{W}" height="4" fill="{ACCENT}"/>')

    # brand
    parts.append(
        f'<text x="72" y="96" font-family={FONT!r} font-size="44" font-weight="bold" letter-spacing="2">'
        f'<tspan fill="{ACCENT}">D</tspan><tspan fill="{ACCENT_2}">M</tspan><tspan fill="{WARN}">V</tspan>'
        f'<tspan fill="{MUTED}"> //</tspan><tspan fill="{TEXT}"> CRIME WATCH</tspan></text>')
    parts.append(
        f'<text x="72" y="134" font-family={FONT!r} font-size="22" fill="{MUTED}" '
        f'letter-spacing="4">DAILY BRIEF</text>')

    latest_day = digest.get("latest_day")
    total = digest.get("total") or 0
    if not latest_day:
        parts.append(
            f'<text x="72" y="330" font-family={FONT!r} font-size="40" fill="{TEXT_2}">'
            f'No data published yet.</text>')
        parts.append("</svg>")
        return "".join(parts)

    # headline: date + total
    parts.append(
        f'<text x="72" y="205" font-family={FONT!r} font-size="30" fill="{TEXT_2}">'
        f'{escape(_fmt_day(latest_day))}</text>')
    parts.append(
        f'<text x="72" y="330" font-family={FONT!r} font-size="112" font-weight="bold" fill="{TEXT}">'
        f'{total:,}</text>')
    avg = digest.get("trailing_avg_same_weekday")
    context = "reported incidents across the DMV"
    if avg:
        context += f" (typical: {avg:.0f})"
    parts.append(
        f'<text x="72" y="376" font-family={FONT!r} font-size="26" fill="{TEXT_2}">'
        f'{escape(context)}</text>')

    # per-jurisdiction strip
    x = 72
    for row in digest.get("by_jurisdiction", [])[:4]:
        label = JURISDICTION_LABELS.get(row["jurisdiction"], row["jurisdiction"])
        parts.append(
            f'<text x="{x}" y="440" font-family={FONT!r} font-size="34" font-weight="bold" '
            f'fill="{ACCENT}">{row["count"]:,}</text>')
        parts.append(
            f'<text x="{x}" y="468" font-family={FONT!r} font-size="18" fill="{MUTED}">'
            f'{escape(label)}</text>')
        x += 270

    # category bars, right side
    cats = digest.get("by_category", [])[:5]
    if cats:
        bx, by, bw = 700, 180, 300
        max_n = max(c["count"] for c in cats) or 1
        parts.append(
            f'<text x="{bx}" y="{by - 18}" font-family={FONT!r} font-size="18" fill="{MUTED}" '
            f'letter-spacing="3">BY CATEGORY</text>')
        for i, c in enumerate(cats):
            y = by + i * 42
            w = max(6, int(bw * c["count"] / max_n))
            color = CATEGORY_COLORS.get(c["offense_category"], CATEGORY_COLORS["other"])
            label = CATEGORY_LABELS.get(c["offense_category"], c["offense_category"])
            parts.append(f'<rect x="{bx}" y="{y}" width="{w}" height="24" fill="{color}" rx="3"/>')
            parts.append(
                f'<text x="{bx + w + 12}" y="{y + 18}" font-family={FONT!r} font-size="19" '
                f'fill="{TEXT}">{escape(label)} {c["count"]:,}</text>')

    # footer
    parts.append(f'<rect x="0" y="{H - 74}" width="{W}" height="74" fill="{SURFACE}"/>')
    parts.append(f'<line x1="0" y1="{H - 74}" x2="{W}" y2="{H - 74}" stroke="{BORDER}"/>')
    parts.append(
        f'<text x="72" y="{H - 28}" font-family={FONT!r} font-size="20" fill="{TEXT_2}">'
        f'heinlh.github.io/dmv-crime-pipeline &#183; public agency data, updated daily</text>')
    parts.append("</svg>")
    return "".join(parts)


def run() -> None:
    digest_path = SITE_DATA_DIR / "digest.json"
    if not digest_path.exists():
        logger.warning("digest.json not found; skipping OG card")
        return
    digest = json.loads(digest_path.read_text())
    OG_DIR.mkdir(parents=True, exist_ok=True)
    svg = build_svg(digest)
    out = OG_DIR / "daily.png"
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out),
                     output_width=W, output_height=H)
    logger.info("Wrote %s (%.1f KB)", out, out.stat().st_size / 1024)


if __name__ == "__main__":
    run()
