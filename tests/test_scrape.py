"""Scraper tests run against a frozen fixture, never the live site.

Every assertion is a real value pulled from bellhaven's pages during recon.
"""
import json

import pytest

from bellhaven.scrape import SiteLocation, parse_detail


def load_fixture():
    return [SiteLocation(**d) for d in json.load(open("tests/fixtures/site.json"))]


def test_scraper_finds_35_including_findlay():
    """The directory advertises 34 across 3 pages. Bellhaven Meadows of Findlay
    has a live detail page but is linked ONLY from the homepage. A scraper that
    walks /communities alone silently loses it."""
    locs = load_fixture()
    assert len(locs) == 35, "expected 34 directory + 1 homepage-only (Findlay)"
    assert "bellhaven-meadows-of-findlay" in {l.slug for l in locs}


def test_detail_fields_parsed():
    toledo = next(l for l in load_fixture() if l.slug == "bellhaven-woods-of-toledo")
    assert toledo.name == "Bellhaven Woods of Toledo"
    assert toledo.street == "4850 NW Sylvania Ave"
    assert (toledo.city, toledo.state, toledo.zip) == ("Toledo", "OH", "43623")
    assert toledo.care == ["Memory Support"]
    assert toledo.phone == "(734) 388-8242"


def test_multi_offering_parsed_as_list():
    """Erie lists two care badges; the CRM's care_type holds one. Keeping the
    list intact here is what lets classify.py decide how to collapse it."""
    erie = next(l for l in load_fixture() if l.slug == "bellhaven-shores-of-erie")
    assert erie.care == ["Assisted Living", "Memory Support"]


def test_ampersand_survives_entity_decoding():
    gc = next(l for l in load_fixture() if l.slug.startswith("bellhaven-rehabilitation"))
    assert gc.name == "Bellhaven Rehabilitation & Nursing of Grove City"


def test_every_location_has_a_usable_address():
    for l in load_fixture():
        assert l.street and l.city and l.state and l.zip, f"{l.slug} missing address"
        assert len(l.zip) == 5 and l.zip.isdigit(), f"{l.slug} bad zip {l.zip!r}"
        assert len(l.state) == 2, f"{l.slug} bad state {l.state!r}"


def test_parse_detail_handles_missing_optional_blocks():
    """Administrator/phone are optional in principle; a missing block must not
    take the whole run down."""
    html = """<html><body><h1>Test Community</h1><dl class="detail">
      <dt>Address</dt><dd>1 Main St<br>Springfield, OH 45503</dd>
      <dt>Care Offerings</dt><dd><span class="badge">Assisted Living</span></dd>
    </dl></body></html>"""
    loc = parse_detail(html, "test-community")
    assert loc.name == "Test Community"
    assert loc.street == "1 Main St"
    assert (loc.city, loc.state, loc.zip) == ("Springfield", "OH", "45503")
    assert loc.phone == ""
