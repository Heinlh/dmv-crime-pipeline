"""Browser fixtures: build the real site from a real pipeline run against
the faked agencies, serve it over HTTP, and drive it in Chromium.

External resources are made deterministic:
  - Esri basemap tiles get a flat grey stub tile
  - Google Fonts get an empty stylesheet (system fonts are fine here)
  - the CDN libraries (Leaflet, markercluster, Chart.js) load from the
    network, or from TEST_VENDOR_DIR when it is set: a directory holding
    node_modules/ with those packages, for environments without egress

PW_CHROMIUM_EXECUTABLE points Playwright at a specific Chromium build
when the one it bundles is not installed.
"""

import base64
import json
import os
import shutil
import threading
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import config
import run_pipeline
from export import render_og_card
from tests.fakes import build_world, epoch_ms, iso

ROOT = Path(__file__).resolve().parents[2]

# a 1x1 grey PNG standing in for every basemap tile
TILE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGPw9vYGAAJCATwr5eOqAAAAAElFTkSuQmCC")


# Records the site must handle, published around the two runs below, by
# the case number a visitor searches for
LATE_MOCO = "26059777"       # published on day 2, offense 20 days earlier
CORRECTED_MOCO = "26050001"  # theft from auto on day 1, robbery on day 2
LATE_PWC = "PD260007777"     # added on day 2, occurred 25 days earlier
HISTORY_START_LABEL = "Jul 2, 2016"


def _moco(incident, crime, start, **extra):
    return {"incident_id": incident, "offence_code": "9999", "case_number": incident[-8:],
            "crimename2": crime,
            "start_date": iso(start), "location": "800 BLK  ELLSWORTH DR",
            "latitude": "38.9967", "longitude": "-77.0261", **extra}


@pytest.fixture(scope="session")
def built_site(tmp_path_factory):
    """site/ plus data/ exported by two days of run_pipeline.main() over
    the fakes: day 1 the base world (plus a 2016 record and a typo-dated
    1940 one), day 2 after a late report, a late Prince William offense
    and a reclassification were published."""
    work = tmp_path_factory.mktemp("built")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "RAW_DIR", work / "raw")
        mp.setattr(config, "STATE_PATH", work / "state" / "watermarks.json")
        mp.setattr(config, "WAREHOUSE_PATH", work / "warehouse" / "crime.duckdb")
        mp.setattr(config, "SITE_DATA_DIR", work / "site" / "data")
        mp.setattr(render_og_card, "OG_DIR", work / "site" / "og")
        world = build_world().install(mp)
        shutil.copytree(ROOT / "site", work / "site",
                        ignore=shutil.ignore_patterns("data", "og"))
        moco_url = config.MOCO["base_url"]
        world.publish(moco_url, _moco("201000001", "Theft", datetime(2016, 7, 2, 10)))
        world.publish(moco_url, _moco("194000001", "Theft", datetime(1940, 2, 17)))
        assert run_pipeline.main() == 0

        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        world.publish(moco_url, _moco("2013" + LATE_MOCO[-5:], "Burglary/Breaking and Entering",
                                      now - timedelta(days=20), case_number=LATE_MOCO))
        original = next(r for r in world.socrata[moco_url]["records"]
                        if r["case_number"] == CORRECTED_MOCO)
        world.publish(moco_url, {**{k: v for k, v in original.items() if not k.startswith(":")},
                                 "crimename2": "Robbery", "crimename3": "ROBBERY - STREET"},
                      replace=lambda r: r["case_number"] == CORRECTED_MOCO)
        world.publish(f"{config.PWC_VA['base_url']}/0", {"attributes": {
            "InstanceID": "pwc-77", "CaseNo": LATE_PWC,
            "OccurredOn": epoch_ms(now - timedelta(days=25)), "BlockAddress": "4300 Dale Blvd",
            "City": "DALE CITY", "IBRCode": "BURGLARY", "CrimeCategory": "Burglary"},
            "geometry": {"x": -77.31, "y": 38.64}})
        assert run_pipeline.main() == 0
    return work / "site"


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def server(built_site):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(built_site)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        exe = os.environ.get("PW_CHROMIUM_EXECUTABLE") or None
        chromium = pw.chromium.launch(executable_path=exe)
        yield chromium
        chromium.close()


def _vendor_file(url: str) -> Path | None:
    """unpkg.com/leaflet@1.9.4/dist/leaflet.js -> node_modules/leaflet/dist/leaflet.js"""
    vendor = os.environ.get("TEST_VENDOR_DIR")
    if not vendor:
        return None
    path = url.split("://", 1)[1].split("/", 1)[1].removeprefix("npm/")
    package, _, rest = path.partition("/")
    candidate = Path(vendor) / "node_modules" / package.rsplit("@", 1)[0] / rest
    for option in (candidate, candidate.with_name(candidate.name.replace(".min.", "."))):
        if option.exists():
            return option
    return None


def _serve_cdn(route):
    local = _vendor_file(route.request.url)
    if local:
        route.fulfill(path=str(local))
    else:
        route.continue_()


@pytest.fixture
def page(browser):
    """A fresh page that records console errors and uncaught exceptions
    in page.errors. Service workers are blocked so every request passes
    through the routes above."""
    context = browser.new_context(service_workers="block", locale="en-US",
                                  viewport={"width": 1280, "height": 900})
    context.route("**/server.arcgisonline.com/**",
                  lambda r: r.fulfill(status=200, content_type="image/png", body=TILE))
    # an empty stylesheet rather than an abort: Chromium logs aborted
    # resources as console errors, which would mask real ones
    context.route("**/fonts.googleapis.com/**",
                  lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    context.route("**/unpkg.com/**", _serve_cdn)
    context.route("**/cdn.jsdelivr.net/**", _serve_cdn)
    tab = context.new_page()
    tab.errors = []
    tab.on("console", lambda m: m.type == "error" and tab.errors.append(m.text))
    tab.on("pageerror", lambda e: tab.errors.append(f"pageerror: {e}"))
    yield tab
    context.close()


@pytest.fixture(scope="session")
def site_data(built_site):
    """The exported JSON payloads, as the browser receives them."""
    return {p.stem: json.loads(p.read_text()) for p in (built_site / "data").glob("*.json")}
