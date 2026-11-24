"""Findings 1 to 3 as a visitor sees them: a late report and a late
Prince William offense are on the map and in Events, a reclassified
incident shows its corrected offense, and the front page dates the
history from 2016, not from a typo-dated 1940 record."""

import pytest
from playwright.sync_api import expect

from tests.browser.conftest import CORRECTED_MOCO, HISTORY_START_LABEL, LATE_MOCO, LATE_PWC

pytestmark = pytest.mark.browser


def test_front_page_dates_the_history_from_2016(page, server, site_data):
    page.goto(f"{server}/index.html")
    card = page.locator("#kpi-cards .card", has_text="Records Since 2016")
    expect(card).to_contain_text(f"warehouse starts {HISTORY_START_LABEL}")
    assert "1940" not in card.inner_text()
    # the count is the in-history total, without the 1940 record
    total = site_data["summary"]["total_records"]
    expect(card.locator(".value")).to_have_text(f"{total:,}")
    assert site_data["summary"]["records_outside_history"] == 1
    assert page.errors == []


@pytest.mark.parametrize("case, label", [(LATE_MOCO, "BURGLARY"), (LATE_PWC, "BURGLARY")])
def test_late_records_are_in_events(page, server, case, label):
    page.goto(f"{server}/events.html")
    page.wait_for_selector("#event-grid .event-card")
    page.fill("#f-search", case)
    expect(page.locator("#event-grid .event-card")).to_have_count(1)
    assert label in page.locator("#event-grid .event-card").first.inner_text().upper()


@pytest.mark.parametrize("case", [LATE_MOCO, LATE_PWC])
def test_late_records_are_on_the_map(page, server, case):
    page.goto(f"{server}/index.html")
    expect(page.locator("#result-count")).to_contain_text("shown")
    page.fill("#map-search-input", f"#{case}")
    page.keyboard.press("Enter")
    page.locator(".leaflet-popup-content").wait_for()
    assert page.locator("#result-count").inner_text().startswith("1 incident")


def test_a_reclassified_incident_shows_the_correction(page, server):
    page.goto(f"{server}/events.html")
    page.wait_for_selector("#event-grid .event-card")
    page.fill("#f-search", CORRECTED_MOCO)
    cards = page.locator("#event-grid .event-card")
    expect(cards).to_have_count(1)
    text = cards.first.inner_text().upper()
    assert "ROBBERY" in text
    assert "THEFT FROM MOTOR VEHICLE" not in text
