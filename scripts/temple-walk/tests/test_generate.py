import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import generate  # noqa: E402


# ── haversine_km ──────────────────────────────────────────────────────────────

def test_haversine_zero_distance():
    assert generate.haversine_km(13.75, 100.49, 13.75, 100.49) == 0.0


def test_haversine_one_degree_longitude_at_equator():
    # 1° of longitude at the equator ≈ 111.19 km
    d = generate.haversine_km(0.0, 0.0, 0.0, 1.0)
    assert abs(d - 111.19) < 0.5


def test_haversine_grand_palace_to_wat_arun():
    # Grand Palace (13.7500, 100.4913) → Wat Arun (13.7437, 100.4889) ≈ 0.75 km
    d = generate.haversine_km(13.7500, 100.4913, 13.7437, 100.4889)
    assert 0.5 < d < 1.0


# ── parse_start ───────────────────────────────────────────────────────────────

def test_parse_start_valid_latlng():
    assert generate.parse_start("13.7516,100.4927") == (13.7516, 100.4927)


def test_parse_start_with_spaces():
    assert generate.parse_start(" 13.7516 , 100.4927 ") == (13.7516, 100.4927)


def test_parse_start_address_returns_none():
    assert generate.parse_start("Democracy Monument, Bangkok") is None


def test_parse_start_out_of_range_returns_none():
    assert generate.parse_start("113.7,100.4") is None


def test_parse_start_single_token_returns_none():
    assert generate.parse_start("Bangkok") is None


# ── slugify ───────────────────────────────────────────────────────────────────

def test_slugify_temple_name():
    assert generate.slugify("Wat Phra Chetuphon (Wat Pho)") == "wat-phra-chetuphon-wat-pho"
