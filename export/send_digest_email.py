"""Send the daily digest as a designed HTML email via the Buttondown API.

Runs at the end of the pipeline workflow. Intentionally a no-op unless
BUTTONDOWN_API_KEY is set (as a repository secret), so the pipeline
works with or without the email layer. Subscriber addresses live only
in Buttondown; this script never sees them.

The email is built from site/data/digest.json, the same artifact that
powers the Daily Brief page, so the email and the page can never
disagree. Two parts:

  1. A narrative written in the site's editorial voice: precise,
     analytical, confident, and slightly dramatic. Every sentence is
     composed from computed facts (totals, averages, shares); no detail
     is ever invented, and homicides are reported soberly, never
     dramatized.
  2. An email-safe HTML layout (tables, inline styles, no scripts or
     external images) in the site's retro neon design language.
"""

import html
import json
import logging
import os
import re
import sys
from datetime import date

import requests

from config import SITE_DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s digest-email: %(message)s")
logger = logging.getLogger(__name__)

BUTTONDOWN_API = "https://api.buttondown.com/v1/emails"

# Keep in sync with site/js/common.js (emails render no JS).
JURISDICTION_LABELS = {
    "dc": "Washington DC",
    "moco": "Montgomery County",
    "pgc": "Prince George's County",
    "fairfax": "Fairfax County",
}
CATEGORY_LABELS = {
    "homicide": "Homicide / Fatal Violence",
    "violent": "Violent Crime",
    "sexual": "Sexual Offenses",
    "property": "Property Crime",
    "vehicle": "Vehicle-Related Crime",
    "disorder": "Drug / Alcohol / Disorder",
    "other": "Other / Unknown",
}
CATEGORY_COLORS = {
    "homicide": "#cd3a3a",
    "violent": "#bf4f00",
    "sexual": "#aa6ad1",
    "property": "#0072B2",
    "vehicle": "#009E73",
    "disorder": "#c98500",
    "other": "#8A8A8A",
}

# Friendly offense titles, ported from site/js/common.js: composed only
# from published fields, the agency's own label always shown alongside.
OFFENSE_LABEL_RULES = [
    (r"HOMICIDE|MURDER|MANSLAUGHTER", "Homicide"),
    (r"SEX ABUSE|RAPE|SEX OFFENSE|SODOMY|FONDLING|PEEPING", "Sexual assault or abuse"),
    (r"CARJACK", "Carjacking"),
    (r"THEFT F/AUTO|THEFT FROM AUTO|FROM MOTOR VEHICLE", "Theft from a parked vehicle"),
    (r"MOTOR VEHICLE THEFT|AUTO, STOLEN|STOLEN VEHICLE", "Stolen vehicle"),
    (r"ASSAULT W/DANGEROUS WEAPON|AGGRAVATED ASSAULT|ASSAULT, WEAPON", "Assault with a weapon"),
    (r"ASSAULT|INTIMIDATION", "Assault or threats"),
    (r"ROBBERY", "Robbery"),
    (r"KIDNAP", "Kidnapping"),
    (r"BURGLARY|B & E|BREAKING", "Burglary / break-in"),
    (r"SHOPLIFT", "Shoplifting"),
    (r"STOLEN PROP", "Stolen property"),
    (r"THEFT|LARCENY", "Theft"),
    (r"FRAUD|SWINDLE|FALSE PRETENSE|FORGERY|COUNTERFEIT", "Fraud"),
    (r"VANDAL|DESTRUCTION", "Vandalism / property damage"),
    (r"ARSON", "Arson"),
    (r"WEAPON", "Weapons offense"),
    (r"DRUG|NARCOT", "Drug offense"),
    (r"DUI|DRIVING UNDER", "Driving under the influence"),
    (r"LIQUOR|ALCOHOL", "Alcohol violation"),
    (r"DISORDERLY", "Disorderly conduct"),
    (r"ACCIDENT", "Traffic accident"),
]


def friendly_offense(raw: str | None) -> str:
    if not raw:
        return "Unknown offense"
    upper = str(raw).upper()
    for pattern, label in OFFENSE_LABEL_RULES:
        if re.search(pattern, upper):
            return label
    return str(raw).capitalize()


def incident_title(inc: dict) -> str:
    title = friendly_offense(inc.get("offense_raw"))
    method = inc.get("method")
    if method and "OTHER" not in str(method).upper():
        title += f" ({str(method).lower()})"
    return title


# ------------------------------------------------------- the narrative

def build_narrative(digest: dict) -> list[str]:
    """Compose the brief in the newsletter's voice: precise, analytical,
    confident, slightly dramatic. Every clause traces to a number in the
    digest; the drama lives in the phrasing, never in the facts."""
    paras = []
    day = date.fromisoformat(digest["latest_day"])
    weekday = day.strftime("%A")
    datestr = f"{weekday}, {day.strftime('%B')} {day.day}"
    total = digest["total"]
    avg = digest.get("trailing_avg_same_weekday")

    if avg:
        ratio = total / avg
        if ratio > 1.15:
            paras.append(
                f"The record for {datestr} closes at {total:,} reported incidents, and the figure "
                f"deserves attention: the eight preceding {weekday}s averaged {avg:.0f}. One day "
                f"proves nothing, but a deviation of this size earns a note in the margin.")
        elif ratio < 0.85:
            paras.append(
                f"The record for {datestr} closes at {total:,} reported incidents, a distinctly "
                f"quiet {weekday} by recent standards; the eight before it averaged {avg:.0f}. "
                f"Quiet days are welcome. They are not yet a trend.")
        else:
            paras.append(
                f"The record for {datestr} closes at {total:,} reported incidents, squarely in "
                f"character for a {weekday}: the eight preceding averaged {avg:.0f}. Nothing in "
                f"the total demands alarm; nothing in it excuses inattention.")
    else:
        paras.append(f"The record for {datestr} closes at {total:,} reported incidents.")

    cats = digest.get("by_category", [])
    if cats and total:
        top = cats[0]
        top_label = CATEGORY_LABELS.get(top["offense_category"], top["offense_category"])
        share = top["count"] / total * 100
        sentence = (
            f"{top_label} carries the largest share of the ledger: {top['count']:,} incidents, "
            f"{share:.0f} percent of the day.")
        if len(cats) > 1:
            second = cats[1]
            second_label = CATEGORY_LABELS.get(second["offense_category"], second["offense_category"])
            sentence += f" {second_label} follows at {second['count']:,}."
        paras.append(sentence)

    jur = digest.get("by_jurisdiction", [])
    if len(jur) > 1:
        counts = [r["count"] for r in jur]
        opener = ("The geography is lopsided." if max(counts) >= 2 * max(min(counts), 1)
                  else "The geography is fairly even.")
        listing = "; ".join(
            f"{JURISDICTION_LABELS.get(r['jurisdiction'], r['jurisdiction'])} logged {r['count']:,}"
            for r in jur)
        paras.append(f"{opener} {listing}.")
    elif len(jur) == 1:
        only = jur[0]
        paras.append(
            f"Every incident in this brief belongs to "
            f"{JURISDICTION_LABELS.get(only['jurisdiction'], only['jurisdiction'])}.")
    if not any(r["jurisdiction"] == "pgc" for r in jur):
        paras.append(
            "Prince George's County publishes on a weekly cadence; its most recent days arrive "
            "late and will take their place in a later brief.")

    signals = digest.get("signals", [])
    if signals:
        described = []
        for s in signals[:3]:
            s_jur = JURISDICTION_LABELS.get(s["jurisdiction"], s["jurisdiction"])
            s_cat = CATEGORY_LABELS.get(s["offense_category"], s["offense_category"])
            if s["direction"] == "spike":
                described.append(
                    f"{s_cat} in {s_jur} ran {s['ratio']:.1f} times its usual {weekday} "
                    f"({s['count']} against an average of {s['baseline']:.0f})")
            else:
                described.append(
                    f"{s_cat} in {s_jur} fell to {s['count']} against an average of "
                    f"{s['baseline']:.0f}")
        opener = ("one figure steps out of line: " if len(described) == 1
                  else f"{len(described)} figures step out of line: ")
        paras.append(
            f"Set against eight weeks of {weekday}s, {opener}"
            f"{'; '.join(described)}. Single days make noise, not verdicts, "
            f"but these are the deviations worth watching.")

    homicide = next((r for r in cats if r["offense_category"] == "homicide"), None)
    if homicide:
        n = homicide["count"]
        if n == 1:
            paras.append(
                "One homicide is recorded in the day's data. It leads the incident list below "
                "and is reported there without embellishment.")
        else:
            paras.append(
                f"{n} homicides are recorded in the day's data. They lead the incident list "
                f"below and are reported there without embellishment.")

    last14 = digest.get("last14", [])
    if len(last14) >= 7:
        peak = max(last14, key=lambda r: r["count"])
        if peak["date"] != digest["latest_day"]:
            peak_day = date.fromisoformat(peak["date"])
            paras.append(
                f"Across the fortnight, {peak_day.strftime('%B')} {peak_day.day} remains the "
                f"high-water mark at {peak['count']:,} incidents. The shape of the two weeks is "
                f"laid out in full on the site.")

    return paras


# ----------------------------------------------------- the HTML layout

# Email-safe subset of the site theme (inline styles only; lines start
# unindented so Markdown processing never mistakes them for code blocks).
INK = "#eaf0fb"
INK2 = "#a6b0c9"
MUTED = "#7b85a1"
BG = "#0a0a14"
SURFACE = "#10101d"
SURFACE2 = "#181629"
BORDER = "#232138"
CYAN = "#4de3ff"
MAGENTA = "#d17bff"
AMBER = "#ffb454"
FONT = "Arial, Helvetica, sans-serif"
MONO = "'Courier New', Courier, monospace"


def _notable_block(inc: dict) -> str:
    color = CATEGORY_COLORS.get(inc.get("offense_category"), CATEGORY_COLORS["other"])
    label = CATEGORY_LABELS.get(inc.get("offense_category"), "Unknown")
    where = html.escape(inc.get("block_address") or "Location withheld")
    area = f" &middot; {html.escape(inc['area_name'])}" if inc.get("area_name") else ""
    jur = JURISDICTION_LABELS.get(inc.get("jurisdiction"), inc.get("jurisdiction", ""))
    agency = html.escape(inc.get("offense_raw") or "n/a")
    return f"""<tr><td style="padding:0 0 10px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{SURFACE2};border-left:3px solid {color};border-radius:4px;">
<tr><td style="padding:10px 14px;">
<span style="font-family:{MONO};font-size:10px;letter-spacing:1px;color:{color};">&#9679;</span>
<span style="font-family:{MONO};font-size:10px;letter-spacing:1px;color:{INK2};text-transform:uppercase;">{label}</span><br>
<span style="font-family:{FONT};font-size:15px;font-weight:bold;color:{INK};">{html.escape(incident_title(inc))}</span><br>
<span style="font-family:{FONT};font-size:13px;color:{INK2};">{where}{area} &middot; {jur}</span><br>
<span style="font-family:{MONO};font-size:10px;color:{MUTED};">AGENCY LABEL: {agency}</span>
</td></tr></table>
</td></tr>"""


def build_html(digest: dict, site_url: str) -> str:
    day = date.fromisoformat(digest["latest_day"])
    datestr = day.strftime("%A, %B ") + str(day.day) + day.strftime(", %Y")
    total = digest["total"]
    avg = digest.get("trailing_avg_same_weekday")
    cats = digest.get("by_category", [])
    top_label = (CATEGORY_LABELS.get(cats[0]["offense_category"], "n/a") if cats else "n/a")

    paragraphs = "".join(
        f'<p style="font-family:{FONT};font-size:15px;line-height:1.65;color:{INK2};margin:0 0 14px 0;">{html.escape(p)}</p>'
        for p in build_narrative(digest))

    max_cat = max((c["count"] for c in cats), default=1)
    category_rows = "".join(f"""<tr>
<td style="padding:4px 0;font-family:{FONT};font-size:13px;color:{INK2};white-space:nowrap;">
<span style="color:{CATEGORY_COLORS.get(c['offense_category'], '#8A8A8A')};">&#9679;</span>
&nbsp;{CATEGORY_LABELS.get(c['offense_category'], c['offense_category'])}</td>
<td width="45%" style="padding:4px 8px;">
<div style="background:{CATEGORY_COLORS.get(c['offense_category'], '#8A8A8A')};height:8px;border-radius:4px;width:{max(6, round(c['count'] / max_cat * 100))}%;"></div></td>
<td align="right" style="padding:4px 0;font-family:{MONO};font-size:13px;color:{INK};">{c['count']:,}</td>
</tr>""" for c in cats)

    notable_blocks = "".join(_notable_block(inc) for inc in digest.get("notable", []))

    signals = digest.get("signals", [])
    signal_rows = "".join(f"""<tr><td style="padding:3px 0;">
<span style="font-family:{MONO};font-size:11px;letter-spacing:1px;color:{AMBER if s['direction'] == 'spike' else CYAN};">{'&#9650; SPIKE' if s['direction'] == 'spike' else '&#9660; LULL'}</span>
<span style="font-family:{FONT};font-size:13px;color:{INK2};">&nbsp;{CATEGORY_LABELS.get(s['offense_category'], s['offense_category'])} &middot; {JURISDICTION_LABELS.get(s['jurisdiction'], s['jurisdiction'])}: {s['count']} vs a typical {s['baseline']:.0f}</span>
</td></tr>""" for s in signals[:4])
    signals_section = f"""<tr><td style="padding:12px 28px 6px 28px;">
<p style="font-family:{FONT};font-size:15px;font-weight:bold;color:{INK};margin:0 0 2px 0;">Signals</p>
<p style="font-family:{FONT};font-size:12px;color:{MUTED};margin:0 0 8px 0;">Each slice measured against its own same-weekday average over the prior 8 weeks.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{signal_rows}</table>
</td></tr>""" if signals else ""

    def stat(value, label):
        return f"""<td align="center" style="padding:12px 6px;background:{SURFACE2};border-radius:6px;">
<div style="font-family:{FONT};font-size:24px;font-weight:bold;color:{INK};">{value}</div>
<div style="font-family:{MONO};font-size:9px;letter-spacing:2px;color:{MUTED};text-transform:uppercase;padding-top:3px;">{label}</div>
</td>"""

    def button(href, text):
        return f"""<a href="{href}" style="display:inline-block;font-family:{MONO};font-size:12px;letter-spacing:1px;color:{CYAN};text-decoration:none;border:1px solid {CYAN};border-radius:5px;padding:9px 16px;margin:0 6px 8px 0;">{text}</a>"""

    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:{SURFACE};border:1px solid {BORDER};border-radius:8px;">
<tr><td style="padding:26px 28px 6px 28px;border-bottom:1px solid {BORDER};">
<span style="font-family:{FONT};font-size:24px;font-weight:bold;letter-spacing:2px;"><span style="color:{CYAN};">D</span><span style="color:{MAGENTA};">M</span><span style="color:{AMBER};">V</span></span>
<span style="font-family:{FONT};font-size:15px;font-weight:bold;letter-spacing:2px;color:{INK};">&nbsp;// CRIME WATCH</span>
<p style="font-family:{MONO};font-size:11px;letter-spacing:2px;color:{CYAN};margin:8px 0 14px 0;">DAILY BRIEF // {datestr.upper()}</p>
</td></tr>
<tr><td style="padding:22px 28px 8px 28px;">
{paragraphs}
</td></tr>
<tr><td style="padding:4px 28px 18px 28px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="6">
<tr>{stat(f"{total:,}", "Reported")}{stat(f"{avg:.0f}" if avg else "n/a", f"Typical {day.strftime('%a')}")}{stat(top_label.split(" / ")[0], "Top category")}</tr>
</table>
</td></tr>
<tr><td style="padding:0 28px 6px 28px;">
<p style="font-family:{FONT};font-size:15px;font-weight:bold;color:{INK};margin:0 0 6px 0;">The day by category</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{category_rows}</table>
</td></tr>
{signals_section}
<tr><td style="padding:16px 28px 4px 28px;">
<p style="font-family:{FONT};font-size:15px;font-weight:bold;color:{INK};margin:0 0 2px 0;">The incidents that lead the list</p>
<p style="font-family:{FONT};font-size:12px;color:{MUTED};margin:0 0 12px 0;">Ranked by this project's severity weighting. Every detail below comes from the agency's published record.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{notable_blocks}</table>
</td></tr>
<tr><td align="center" style="padding:10px 28px 20px 28px;">
{button(f"{site_url}/index.html", "OPEN THE MAP")}{button(f"{site_url}/daily.html", "FULL BRIEF + CHARTS")}{button(f"{site_url}/trends.html", "TRENDS SINCE 2016")}
</td></tr>
<tr><td style="padding:16px 28px 22px 28px;border-top:1px solid {BORDER};">
<p style="font-family:{FONT};font-size:11px;line-height:1.6;color:{MUTED};margin:0;">
Data as of last published agency data, not live. Reports are preliminary and subject to change by the agencies; locations are block-level as published. Category labels are this project's editorial mapping; the agency's own label is shown on every incident.
<a href="{site_url}/privacy.html" style="color:{MUTED};">Privacy policy</a> &middot;
<a href="{{{{ unsubscribe_url }}}}" style="color:{MUTED};">Unsubscribe</a>
</p>
</td></tr>
</table>
</td></tr></table>"""


# ------------------------------------------------------------- sending

def run() -> int:
    api_key = os.environ.get("BUTTONDOWN_API_KEY", "").strip()
    if not api_key:
        logger.info("BUTTONDOWN_API_KEY not set; skipping email digest")
        return 0

    digest_path = SITE_DATA_DIR / "digest.json"
    if not digest_path.exists():
        logger.warning("No digest.json found; nothing to send")
        return 0
    digest = json.loads(digest_path.read_text())
    if not digest.get("latest_day"):
        logger.warning("Digest has no data day; nothing to send")
        return 0

    site_url = os.environ.get("SITE_URL", "https://heinlh.github.io/dmv-crime-pipeline").rstrip("/")
    day = date.fromisoformat(digest["latest_day"])
    payload = {
        "subject": f"The day's record: {digest['total']:,} incidents on "
                   f"{day.strftime('%A, %B')} {day.day}",
        "body": build_html(digest, site_url),
        "status": "about_to_send",
    }
    response = requests.post(
        BUTTONDOWN_API,
        json=payload,
        headers={
            "Authorization": f"Token {api_key}",
            # Buttondown's confirmation that API-created emails may send
            # immediately (status 'about_to_send'); required by their API.
            "X-Buttondown-Live-Dangerously": "true",
        },
        timeout=60,
    )
    if response.status_code >= 300:
        # Re-running the workflow re-composes the identical email and
        # Buttondown refuses to send it twice. That refusal is the
        # desired outcome (subscribers get exactly one digest per day),
        # so treat it as success instead of failing the pipeline run.
        try:
            error_code = response.json().get("code", "")
        except ValueError:
            error_code = ""
        if response.status_code == 400 and error_code == "email_duplicate":
            logger.info("Digest for %s was already sent (Buttondown reports a "
                        "duplicate); skipping the re-send", digest["latest_day"])
            return 0
        logger.error("Buttondown API returned %d: %s", response.status_code, response.text[:500])
        return 1
    logger.info("Digest email queued for %s", digest["latest_day"])
    return 0


if __name__ == "__main__":
    sys.exit(run())
