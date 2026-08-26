"""Every case here is real drift observed between the website and the CRM."""
import pytest

from bellhaven.normalize import (is_po_box, name_sim, norm_city, norm_name,
                                 norm_phone, norm_street, norm_zip)


@pytest.mark.parametrize("crm,site,label", [
    ("4850 Northwest Sylvania Avenue", "4850 NW Sylvania Ave", "Toledo"),
    ("1250 Northwest Franklin St", "1250 NW Franklin Street", "Chesterton"),
    ("4930 West Lake Road", "4930 W Lake Rd", "Erie"),
    ("3313 Wilmington Pike", "3313 Wilmington Pk", "Kettering"),
    ("750 Stewart Rd", "750 Stewart Road", "Monroe"),
    ("805 Colegate Drive", "805 Colegate Dr", "Marietta"),
    ("2680 Maple Avenue", "2680 Maple Ave", "Zanesville"),
])
def test_street_abbreviations_converge(crm, site, label):
    assert norm_street(crm) == norm_street(site), label


def test_distinct_streets_stay_distinct():
    """Different house numbers on the same street name must never converge."""
    assert norm_street("4645 Adams Ave") != norm_street("5074 Adams Ave")
    assert norm_street("1673 Lake Rd") != norm_street("3183 Lake Rd")


def test_po_box_detected():
    assert is_po_box("PO Box 517")
    assert is_po_box("P.O. Box 12")
    assert is_po_box("  po box 9  ")
    assert not is_po_box("3156 W Prospect Rd")
    assert not is_po_box("")


def test_zip_is_string_and_padded():
    assert norm_zip("43623") == "43623"
    assert norm_zip("43623-1234") == "43623"
    assert norm_zip(7030) == "07030", "leading zero must survive an int"
    assert norm_zip("") == ""
    assert norm_zip(None) == ""


def test_name_normalization():
    assert norm_name("Bellhaven Rehabilitation & Nursing of Grove City") == \
           norm_name("Bellhaven Rehabilitation and Nursing of Grove City")
    assert norm_name("The Arbors at Bellhaven - Dayton") == \
           norm_name("Arbors at Bellhaven Dayton")


def test_name_sim_scores():
    # A real rename that should be recognisable.
    assert name_sim("Bellhaven Healthcare Centre of Ashland",
                    "Bellhaven Health Care Center of Ashland") >= 70
    # A full rebrand that must NOT clear the confident bar on name alone.
    assert name_sim("Bellhaven of Kettering", "Kettering Care Centre") < 88


def test_name_sim_flags_the_amberly_decoy_as_dangerous():
    """The whole reason match.py gates on city/state before trusting a name:
    these two are different facilities 300 miles apart."""
    assert name_sim("Amberly Manor", "Amberly Care Center") >= 70


def test_phone_digits():
    assert norm_phone("(734) 388-8242") == "7343888242"
    assert norm_phone("734-388-8242") == "7343888242"
    assert norm_phone("") == ""
    assert norm_phone("555") == ""


def test_city_normalization():
    assert norm_city("Grand Rapids") == norm_city("  grand   rapids ")
    assert norm_city("St. Louis") == norm_city("St Louis")
