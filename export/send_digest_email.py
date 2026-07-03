"""Send the daily digest as an email via the Buttondown API.

Runs at the end of the pipeline workflow. Intentionally a no-op unless
BUTTONDOWN_API_KEY is set (as a repository secret), so the pipeline
works with or without the email layer. Subscriber addresses live only
in Buttondown; this script never sees them -- it hands Buttondown the
digest content and the service fans it out to the list, with double
opt-in and unsubscribe links handled by the provider.

The email body is Markdown built from site/data/digest.json, the same
artifact that powers the Daily Brief page, so the email and the page
can never disagree.
"""

import json
import logging
import os
import sys

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


def build_markdown(digest: dict, site_url: str) -> str:
    lines = [f"## DMV Crime Watch daily brief for {digest['latest_day']}", ""]
    for bullet in digest["bullets"]:
        lines.append(f"- {bullet}")
    if digest["notable"]:
        lines += ["", "### Most serious incidents of the day", ""]
        for inc in digest["notable"]:
            label = CATEGORY_LABELS.get(inc["offense_category"], inc["offense_category"])
            where = inc.get("block_address") or "location withheld"
            area = f", {inc['area_name']}" if inc.get("area_name") else ""
            jur = JURISDICTION_LABELS.get(inc["jurisdiction"], inc["jurisdiction"])
            lines.append(
                f"- **{label}**: {inc.get('offense_raw', 'unknown')} "
                f"({where}{area}, {jur})")
    lines += [
        "",
        f"[Interactive map]({site_url}/index.html) &middot; "
        f"[Full daily brief with charts]({site_url}/daily.html) &middot; "
        f"[Trends since 2016]({site_url}/trends.html)",
        "",
        "_Data as of last published agency data, not live. Reports are "
        "preliminary and subject to change by the agencies. Locations are "
        "block-level as published._",
    ]
    return "\n".join(lines)


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
    payload = {
        "subject": f"DMV crime brief: {digest['total']:,} incidents on {digest['latest_day']}",
        "body": build_markdown(digest, site_url),
        "status": "about_to_send",
    }
    response = requests.post(
        BUTTONDOWN_API,
        json=payload,
        headers={"Authorization": f"Token {api_key}"},
        timeout=60,
    )
    if response.status_code >= 300:
        logger.error("Buttondown API returned %d: %s", response.status_code, response.text[:500])
        return 1
    logger.info("Digest email queued for %s", digest["latest_day"])
    return 0


if __name__ == "__main__":
    sys.exit(run())
