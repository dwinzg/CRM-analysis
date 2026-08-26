"""Pulls every Bellhaven community from the public website.

Two things about this site drive the design:

1. `/communities` is paginated (3 pages, 34 cards today) but is NOT the complete
   list. `bellhaven-meadows-of-findlay` has a live detail page linked only from
   the homepage. Discovery therefore unions the directory with the homepage.
2. Card markup carries name/city/care but not the street, so every location
   costs one detail fetch. At 35 pages that is fine.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

import httpx
from selectolax.parser import HTMLParser

from . import config

COMMUNITY_HREF = re.compile(r"^/communities/(?P<slug>[a-z0-9][a-z0-9\-]*)$")


@dataclass
class SiteLocation:
    slug: str
    name: str
    street: str
    city: str
    state: str
    zip: str
    care: list[str] = field(default_factory=list)
    phone: str = ""
    url: str = ""


def _dd_after(tree: HTMLParser, label: str):
    """Return the <dd> following the <dt> whose text matches `label`."""
    for dt in tree.css("dt"):
        if dt.text(strip=True).lower() == label.lower():
            node = dt.next
            while node is not None and node.tag != "dd":
                node = node.next
            return node
    return None


def parse_detail(html: str, slug: str) -> SiteLocation:
    tree = HTMLParser(html)
    heading = tree.css_first("h1")
    if heading is None:
        raise ValueError(f"{slug}: no <h1>, not a community detail page")
    name = heading.text(strip=True)

    addr = _dd_after(tree, "Address")
    if addr is None:
        raise ValueError(f"{slug}: no Address block")
    # "1 Main St<br>Springfield, OH 45503"
    lines = [HTMLParser(part).text(strip=True) for part in addr.html.split("<br>")]
    lines = [l for l in lines if l]
    street = lines[0]
    city_state_zip = lines[-1]
    city, rest = city_state_zip.rsplit(",", 1)
    state, zipcode = rest.split()

    care_dd = _dd_after(tree, "Care Offerings")
    care = [b.text(strip=True) for b in care_dd.css(".badge")] if care_dd is not None else []

    phone_dd = _dd_after(tree, "Phone")
    phone = phone_dd.text(strip=True) if phone_dd is not None else ""

    return SiteLocation(
        slug=slug,
        name=name,
        street=street,
        city=city.strip(),
        state=state.strip().upper(),
        zip=zipcode.strip(),
        care=care,
        phone=phone,
        url=f"{config.BASE_URL}/communities/{slug}",
    )


def _slugs_on(html: str) -> set[str]:
    out = set()
    for a in HTMLParser(html).css("a[href]"):
        m = COMMUNITY_HREF.match(a.attributes.get("href", ""))
        if m:
            out.add(m.group("slug"))
    return out


def discover_slugs(client: httpx.Client) -> list[str]:
    """Union of the paginated directory and the homepage.

    Findlay is reachable only from the homepage, so walking the directory alone
    silently drops a live community. Pagination stops when a page yields nothing
    new, which is safe whether or not the pager markup changes.
    """
    slugs: set[str] = set()
    for page in range(1, 26):  # generous upper bound; loop exits on no-new-slugs
        r = client.get(f"{config.BASE_URL}/communities", params={"page": page})
        r.raise_for_status()
        found = _slugs_on(r.text)
        if not found - slugs:
            break
        slugs |= found

    home = client.get(config.BASE_URL)
    home.raise_for_status()
    slugs |= _slugs_on(home.text)
    return sorted(slugs)


def scrape_all(return_skipped: bool = False):
    """Every community on the site.

    A page that cannot be parsed is skipped and reported rather than raising.
    One edited detail page among the 35 should cost one location, not the
    whole run, since an exception here produces zero proposals and looks
    identical to "nothing changed".
    """
    out: list[SiteLocation] = []
    skipped: list[tuple[str, str]] = []
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for slug in discover_slugs(client):
            try:
                r = client.get(f"{config.BASE_URL}/communities/{slug}")
                r.raise_for_status()
                out.append(parse_detail(r.text, slug))
            except Exception as exc:
                skipped.append((slug, f"{type(exc).__name__}: {exc}"))
    return (out, skipped) if return_skipped else out


if __name__ == "__main__":
    locations, skipped = scrape_all(return_skipped=True)
    for slug, reason in skipped:
        print(f"skipped {slug}: {reason}")
    with open("tests/fixtures/site.json", "w") as fh:
        json.dump([asdict(l) for l in locations], fh, indent=1)
    print(f"{len(locations)} locations -> tests/fixtures/site.json")
