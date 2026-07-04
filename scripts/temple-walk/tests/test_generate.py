import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import lib  # noqa: E402


# ── haversine_km ──────────────────────────────────────────────────────────────

def test_haversine_zero_distance():
    assert lib.haversine_km(13.75, 100.49, 13.75, 100.49) == 0.0


def test_haversine_one_degree_longitude_at_equator():
    # 1° of longitude at the equator ≈ 111.19 km
    d = lib.haversine_km(0.0, 0.0, 0.0, 1.0)
    assert abs(d - 111.19) < 0.5


def test_haversine_grand_palace_to_wat_arun():
    # Grand Palace (13.7500, 100.4913) → Wat Arun (13.7437, 100.4889) ≈ 0.75 km
    d = lib.haversine_km(13.7500, 100.4913, 13.7437, 100.4889)
    assert 0.5 < d < 1.0


# ── parse_start ───────────────────────────────────────────────────────────────

def test_parse_start_valid_latlng():
    assert lib.parse_start("13.7516,100.4927") == (13.7516, 100.4927)


def test_parse_start_with_spaces():
    assert lib.parse_start(" 13.7516 , 100.4927 ") == (13.7516, 100.4927)


def test_parse_start_address_returns_none():
    assert lib.parse_start("Democracy Monument, Bangkok") is None


def test_parse_start_out_of_range_returns_none():
    assert lib.parse_start("113.7,100.4") is None


def test_parse_start_single_token_returns_none():
    assert lib.parse_start("Bangkok") is None


# ── slugify ───────────────────────────────────────────────────────────────────

def test_slugify_temple_name():
    assert lib.slugify("Wat Phra Chetuphon (Wat Pho)") == "wat-phra-chetuphon-wat-pho"


def test_slugify_thai_name_returns_empty():
    assert lib.slugify("วัดโพธิ์") == ""


# ── parse_overpass_elements ───────────────────────────────────────────────────

def test_parse_overpass_node():
    elements = [{"type": "node", "id": 1, "lat": 13.75, "lon": 100.49,
                 "tags": {"name": "Wat Pho"}}]
    temples = lib.parse_overpass_elements(elements)
    assert temples == [{"name": "Wat Pho", "name_en": "Wat Pho", "lat": 13.75, "lng": 100.49,
                        "osm_type": "node", "osm_id": "node/1"}]


def test_parse_overpass_node_prefers_name_en():
    elements = [{"type": "node", "id": 1, "lat": 13.75, "lon": 100.49,
                 "tags": {"name": "วัดโพธิ์", "name:en": "Wat Pho"}}]
    temples = lib.parse_overpass_elements(elements)
    assert temples[0]["name"] == "วัดโพธิ์"
    assert temples[0]["name_en"] == "Wat Pho"


def test_parse_overpass_way_uses_center():
    elements = [{"type": "way", "id": 2, "center": {"lat": 13.74, "lon": 100.48},
                 "tags": {"name": "Wat Arun"}}]
    temples = lib.parse_overpass_elements(elements)
    assert temples[0]["lat"] == 13.74
    assert temples[0]["osm_id"] == "way/2"


def test_parse_overpass_skips_unnamed():
    elements = [{"type": "node", "id": 3, "lat": 13.7, "lon": 100.5, "tags": {}},
                {"type": "node", "id": 4, "lat": 13.7, "lon": 100.5}]
    assert lib.parse_overpass_elements(elements) == []


def test_parse_overpass_skips_missing_center():
    elements = [{"type": "way", "id": 5, "tags": {"name": "Wat Ghost"}}]
    assert lib.parse_overpass_elements(elements) == []


def test_parse_overpass_dedupes_by_name_preferring_way():
    elements = [
        {"type": "node", "id": 6, "lat": 13.70, "lon": 100.50, "tags": {"name": "Wat Saket"}},
        {"type": "way", "id": 7, "center": {"lat": 13.71, "lon": 100.51}, "tags": {"name": "Wat Saket"}},
    ]
    temples = lib.parse_overpass_elements(elements)
    assert len(temples) == 1
    assert temples[0]["osm_id"] == "way/7"


def test_parse_overpass_node_never_replaces_way():
    elements = [
        {"type": "way", "id": 8, "center": {"lat": 13.71, "lon": 100.51}, "tags": {"name": "Wat Saket"}},
        {"type": "node", "id": 9, "lat": 13.70, "lon": 100.50, "tags": {"name": "Wat Saket"}},
    ]
    temples = lib.parse_overpass_elements(elements)
    assert len(temples) == 1
    assert temples[0]["osm_id"] == "way/8"


# ── plan_walk ─────────────────────────────────────────────────────────────────

def fake_leg(lat1, lng1, lat2, lng2):
    """Straight-line 2-point leg with haversine distance."""
    return [[lng1, lat1], [lng2, lat2]], lib.haversine_km(lat1, lng1, lat2, lng2)


def temple(name, lat, lng):
    return {"name": name, "lat": lat, "lng": lng, "osm_type": "node", "osm_id": f"node/{name}"}


def test_plan_walk_chains_nearest_first():
    # On the equator: 0.01° lng ≈ 1.112 km
    temples = [temple("B", 0.0, 0.03), temple("A", 0.0, 0.01), temple("C", 0.0, 0.10)]
    walk = lib.plan_walk((0.0, 0.0), temples, 5.0, fake_leg)
    # start→A (1.11) + A→B (2.22) = 3.34; B→C (7.78) would exceed 5 → stop
    assert [s["name"] for s in walk["stops"]] == ["A", "B"]
    assert walk["stops"][0]["order"] == 1
    assert walk["stops"][1]["order"] == 2
    assert abs(walk["total_km"] - 3.34) < 0.02


def test_plan_walk_cumulative_distance_on_stops():
    temples = [temple("A", 0.0, 0.01), temple("B", 0.0, 0.03)]
    walk = lib.plan_walk((0.0, 0.0), temples, 10.0, fake_leg)
    assert abs(walk["stops"][0]["distance_km"] - 1.11) < 0.02
    assert abs(walk["stops"][1]["distance_km"] - 3.34) < 0.02


def test_plan_walk_first_temple_beyond_budget():
    temples = [temple("Far", 0.0, 0.5)]  # ≈ 55.6 km away
    walk = lib.plan_walk((0.0, 0.0), temples, 10.0, fake_leg)
    assert walk["stops"] == []
    assert walk["route_coords"] == []
    assert walk["total_km"] == 0.0


def test_plan_walk_never_revisits():
    temples = [temple("A", 0.0, 0.01)]
    walk = lib.plan_walk((0.0, 0.0), temples, 100.0, fake_leg)
    assert len(walk["stops"]) == 1


def test_plan_walk_route_junction_dedup_and_segment_breaks():
    temples = [temple("A", 0.0, 0.01), temple("B", 0.0, 0.03)]
    walk = lib.plan_walk((0.0, 0.0), temples, 10.0, fake_leg)
    # leg1 contributes 2 points, leg2 contributes 1 (junction trimmed)
    assert walk["route_coords"] == [[0.0, 0.0], [0.01, 0.0], [0.03, 0.0]]
    # one break per leg start + final terminator (bangkok-citywalk convention)
    assert walk["segment_breaks"] == [0, 2, 2]


def test_plan_walk_exact_budget_leg_is_accepted():
    # A is ≈ 1.112 km away; budget exactly that distance (not strictly greater)
    temples = [temple("A", 0.0, 0.01)]
    dist = lib.haversine_km(0.0, 0.0, 0.0, 0.01)
    walk = lib.plan_walk((0.0, 0.0), temples, dist, fake_leg)
    assert len(walk["stops"]) == 1


# ── resolve_leg ───────────────────────────────────────────────────────────────

def test_resolve_leg_direct_line_when_degenerate():
    def fail_leg(*_):
        raise AssertionError("fetch_leg should not be called for a degenerate leg")

    # ~5.5m apart — well under the 50m degenerate threshold
    coords, km = lib.resolve_leg(0.0, 0.0, 0.00005, 0.0, fail_leg)
    assert coords == [[0.0, 0.0], [0.0, 0.00005]]
    assert km < 0.05


def test_resolve_leg_delegates_when_not_degenerate():
    coords, km = lib.resolve_leg(0.0, 0.0, 0.0, 0.02, fake_leg)
    assert km > 0.05
    assert coords == [[0.0, 0.0], [0.02, 0.0]]


# ── plan_walk: route-aware selection ────────────────────────────────────────

def test_plan_walk_prefers_shortest_routed_over_nearest_haversine():
    # B is nearer by straight line than A, but A's real route is far shorter
    # (e.g. B is haversine-close but blocked by a wall/canal in reality).
    temples = [temple("A", 0.0, 0.02), temple("B", 0.0, 0.01)]

    def stub_leg(lat1, lng1, lat2, lng2):
        km = {0.01: 5.0, 0.02: 0.5}[round(lng2, 2)]
        return [[lng1, lat1], [lng2, lat2]], km

    walk = lib.plan_walk((0.0, 0.0), temples, 10.0, stub_leg)
    assert walk["stops"][0]["name"] == "A"
    assert walk["stops"][0]["distance_km"] == 0.5


def test_plan_walk_only_evaluates_k_nearest_candidates_per_round():
    temples = [temple("A", 0.0, 0.01), temple("B", 0.0, 0.02),
               temple("C", 0.0, 0.03), temple("D", 0.0, 0.04)]
    evaluated = []

    def spy_leg(lat1, lng1, lat2, lng2):
        evaluated.append(round(lng2, 2))
        return fake_leg(lat1, lng1, lat2, lng2)

    lib.plan_walk((0.0, 0.0), temples, 100.0, spy_leg, k_candidates=3)
    # first round only routes the 3 nearest remaining temples, not D
    assert evaluated[:3] == [0.01, 0.02, 0.03]


def test_plan_walk_degenerate_leg_skips_fetch_leg():
    calls = []

    def spy_leg(lat1, lng1, lat2, lng2):
        calls.append((lat1, lng1, lat2, lng2))
        return [[lng1, lat1], [lng2, lat2]], 999.0  # would blow the budget if used

    # ~5.5m away — below the degenerate threshold
    temples = [temple("Same Spot", 0.00005, 0.0)]
    walk = lib.plan_walk((0.0, 0.0), temples, 1.0, spy_leg)
    assert calls == []
    assert len(walk["stops"]) == 1
    assert walk["stops"][0]["distance_km"] < 0.05


def test_stop_slug_latin_name():
    assert lib.stop_slug(temple("Wat Pho", 0.0, 0.0)) == "wat-pho"


def test_stop_slug_thai_name_falls_back_to_osm_id():
    t = {"name": "วัดโพธิ์", "lat": 0.0, "lng": 0.0, "osm_type": "way", "osm_id": "way/123"}
    assert lib.stop_slug(t) == "way-123"


# ── build_geojson / build_content_page ────────────────────────────────────────

def make_walk():
    temples = [temple("Wat A", 0.0, 0.01), temple("Wat B", 0.0, 0.03)]
    return lib.plan_walk((0.0, 0.0), temples, 10.0, fake_leg)


def test_build_geojson_start_point_order_zero():
    fc = lib.build_geojson((0.0, 0.0), make_walk(), "testwalk", {})
    start = fc["features"][0]
    assert start["geometry"] == {"type": "Point", "coordinates": [0.0, 0.0]}
    assert start["properties"]["order"] == 0
    assert start["properties"]["name"] == "Start"


def test_build_geojson_stop_properties():
    photos = {"Wat A": [{"url": "http://x/1.jpg", "attribution": "© Alice / CC"}]}
    fc = lib.build_geojson((0.0, 0.0), make_walk(), "testwalk", photos)
    stop = fc["features"][1]
    p = stop["properties"]
    assert p["name"] == "Wat A"
    assert p["name_en"] == "Wat A"
    assert p["order"] == 1
    assert p["slug"] == "wat-a"
    assert p["osm_id"] == "node/Wat A"
    assert p["photos"] == ["/temple-walks/testwalk/photos/wat-a-1.jpg"]
    assert p["attributions"] == ["© Alice / CC"]
    assert p["distance_km"] > 0


def test_build_geojson_stop_without_photos():
    fc = lib.build_geojson((0.0, 0.0), make_walk(), "testwalk", {})
    p = fc["features"][1]["properties"]
    assert p["photos"] == []
    assert p["attributions"] == []


def test_build_geojson_multi_photo_attributions():
    photos = {"Wat A": [
        {"url": "http://x/1.jpg", "attribution": "© Alice / CC"},
        {"url": "http://x/2.jpg", "attribution": "© Bob / CC-BY"},
    ]}
    fc = lib.build_geojson((0.0, 0.0), make_walk(), "testwalk", photos)
    p = fc["features"][1]["properties"]
    assert p["photos"] == [
        "/temple-walks/testwalk/photos/wat-a-1.jpg",
        "/temple-walks/testwalk/photos/wat-a-2.jpg",
    ]
    assert p["attributions"] == ["© Alice / CC", "© Bob / CC-BY"]


def test_build_geojson_route_linestring():
    walk = make_walk()
    fc = lib.build_geojson((0.0, 0.0), walk, "testwalk", {})
    route = fc["features"][-1]
    assert route["geometry"]["type"] == "LineString"
    assert route["geometry"]["coordinates"] == walk["route_coords"]
    assert route["properties"]["type"] == "route"
    assert route["properties"]["segment_breaks"] == walk["segment_breaks"]
    assert route["properties"]["total_km"] == walk["total_km"]


def test_build_geojson_thai_name_photo_paths_use_osm_id():
    thai = {"name": "วัดโพธิ์", "lat": 0.0, "lng": 0.01, "osm_type": "way", "osm_id": "way/123"}
    walk = lib.plan_walk((0.0, 0.0), [thai], 10.0, fake_leg)
    photos = {"วัดโพธิ์": [{"url": "http://x/1.jpg", "attribution": "© A / CC"}]}
    fc = lib.build_geojson((0.0, 0.0), walk, "testwalk", photos)
    p = fc["features"][1]["properties"]
    assert p["slug"] == "way-123"
    assert p["photos"] == ["/temple-walks/testwalk/photos/way-123-1.jpg"]


def test_build_content_page():
    md = lib.build_content_page("rattanakosin", "13.7516,100.4927", 7, 9.4)
    assert 'title: "Temple Walk — Rattanakosin"' in md
    assert 'description: "7 temples, 9.4 km"' in md
    assert 'type: "temple-walk"' in md
    assert 'geojson: "/temple-walks/rattanakosin/walk.geojson"' in md
