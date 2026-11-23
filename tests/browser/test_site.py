"""The built site in Chromium: the map and search work on data that went
through the allowlisted, coarsened raw zone, and no exact PG County
address reaches a visitor."""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

# Waits use locators, never wait_for_function: that evaluates a string as
# script, which the site's Content-Security-Policy rightly forbids.

PGC_CASE = "PG26000101"          # exact address 9759 GOOD LUCK RD in the fake
PGC_BLOCK = "9700 BLOCK GOOD LUCK RD"
EXACT = "9759"


@pytest.mark.parametrize("path", ["index.html", "events.html", "trends.html", "daily.html",
                                  "methodology.html", "about.html"])
def test_page_loads_cleanly(page, server, path):
    page.goto(f"{server}/{path}", wait_until="networkidle")
    assert page.errors == []


def test_map_plots_every_geocoded_incident(page, server, site_data):
    expected = sum(1 for r in site_data["incidents"]["rows"]
                   if r[site_data["incidents"]["columns"].index("latitude")] is not None)
    page.goto(f"{server}/index.html")
    expect(page.locator("#result-count")).to_contain_text("shown")
    assert page.locator("#result-count").inner_text().startswith(f"{expected} incident")
    assert page.locator(".leaflet-interactive, .marker-cluster").count() > 0
    assert page.errors == []


def test_map_case_lookup_shows_the_block_not_the_house(page, server):
    page.goto(f"{server}/index.html")
    expect(page.locator("#result-count")).to_contain_text("shown")
    page.fill("#map-search-input", f"#{PGC_CASE}")
    page.keyboard.press("Enter")
    popup = page.locator(".leaflet-popup-content")
    popup.wait_for()
    text = popup.inner_text()
    assert PGC_BLOCK in text
    assert EXACT not in text
    assert page.locator("#result-count").inner_text().startswith("1 incident")


def test_events_search_shows_the_block_not_the_house(page, server):
    page.goto(f"{server}/events.html")
    page.wait_for_selector("#event-grid .event-card")
    page.fill("#f-search", PGC_CASE)
    expect(page.locator("#event-grid .event-card")).to_have_count(1)
    card = page.locator("#event-grid .event-card").first.inner_text()
    assert PGC_BLOCK in card
    assert EXACT not in card


def test_no_payload_or_page_carries_an_exact_pgc_address(page, server, built_site):
    # the exact house number in any form, not just the full original string
    house = re.compile(rf"\b{EXACT}\b")
    for payload in (built_site / "data").glob("*.json"):
        assert not house.search(payload.read_text()), payload.name
    page.goto(f"{server}/events.html")
    page.wait_for_selector("#event-grid .event-card")
    assert not house.search(page.content())
