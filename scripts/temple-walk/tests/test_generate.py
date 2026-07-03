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


# ── parse_overpass_elements ───────────────────────────────────────────────────

def test_parse_overpass_node():
    elements = [{"type": "node", "id": 1, "lat": 13.75, "lon": 100.49,
                 "tags": {"name": "Wat Pho"}}]
    temples = generate.parse_overpass_elements(elements)
    assert temples == [{"name": "Wat Pho", "lat": 13.75, "lng": 100.49,
                        "osm_type": "node", "osm_id": "node/1"}]


def test_parse_overpass_way_uses_center():
    elements = [{"type": "way", "id": 2, "center": {"lat": 13.74, "lon": 100.48},
                 "tags": {"name": "Wat Arun"}}]
    temples = generate.parse_overpass_elements(elements)
    assert temples[0]["lat"] == 13.74
    assert temples[0]["osm_id"] == "way/2"


def test_parse_overpass_skips_unnamed():
    elements = [{"type": "node", "id": 3, "lat": 13.7, "lon": 100.5, "tags": {}},
                {"type": "node", "id": 4, "lat": 13.7, "lon": 100.5}]
    assert generate.parse_overpass_elements(elements) == []


def test_parse_overpass_skips_missing_center():
    elements = [{"type": "way", "id": 5, "tags": {"name": "Wat Ghost"}}]
    assert generate.parse_overpass_elements(elements) == []


def test_parse_overpass_dedupes_by_name_preferring_way():
    elements = [
        {"type": "node", "id": 6, "lat": 13.70, "lon": 100.50, "tags": {"name": "Wat Saket"}},
        {"type": "way", "id": 7, "center": {"lat": 13.71, "lon": 100.51}, "tags": {"name": "Wat Saket"}},
    ]
    temples = generate.parse_overpass_elements(elements)
    assert len(temples) == 1
    assert temples[0]["osm_id"] == "way/7"


def test_parse_overpass_node_never_replaces_way():
    elements = [
        {"type": "way", "id": 8, "center": {"lat": 13.71, "lon": 100.51}, "tags": {"name": "Wat Saket"}},
        {"type": "node", "id": 9, "lat": 13.70, "lon": 100.50, "tags": {"name": "Wat Saket"}},
    ]
    temples = generate.parse_overpass_elements(elements)
    assert len(temples) == 1
    assert temples[0]["osm_id"] == "way/8"
