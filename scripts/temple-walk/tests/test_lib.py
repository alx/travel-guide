import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import lib


def fake_leg(lat1, lng1, lat2, lng2):
    return [[lng1, lat1], [lng2, lat2]], lib.haversine_km(lat1, lng1, lat2, lng2)


def temple(name, lat, lng):
    return {"name": name, "lat": lat, "lng": lng, "osm_type": "node", "osm_id": f"node/{name}"}


def test_plan_walk_random_reproducible():
    temples = [temple("A", 0.0, 0.01), temple("B", 0.0, 0.02), temple("C", 0.0, 0.03)]
    w1 = lib.plan_walk_random((0.0, 0.0), temples, 10.0, fake_leg, random.Random(42))
    w2 = lib.plan_walk_random((0.0, 0.0), temples, 10.0, fake_leg, random.Random(42))
    assert [s["name"] for s in w1["stops"]] == [s["name"] for s in w2["stops"]]


def test_plan_walk_random_produces_variation():
    # Three temples almost equidistant — random seed should vary which is chosen first
    temples = [temple("A", 0.0, 0.0100), temple("B", 0.0, 0.0101), temple("C", 0.0, 0.0102)]
    first_stops = set()
    for seed in range(30):
        w = lib.plan_walk_random((0.0, 0.0), list(temples), 10.0, fake_leg, random.Random(seed))
        if w["stops"]:
            first_stops.add(w["stops"][0]["name"])
    assert len(first_stops) > 1, "Expected at least two different first-stop choices across 30 seeds"


def test_plan_walk_random_never_exceeds_budget():
    temples = [temple("A", 0.0, 0.01), temple("B", 0.0, 0.5)]
    for seed in range(10):
        w = lib.plan_walk_random((0.0, 0.0), temples, 5.0, fake_leg, random.Random(seed))
        assert w["total_km"] <= 5.0


def test_plan_walk_random_empty_when_first_temple_beyond_budget():
    temples = [temple("Far", 0.0, 0.5)]  # ~55 km away
    w = lib.plan_walk_random((0.0, 0.0), temples, 10.0, fake_leg, random.Random(0))
    assert w["stops"] == []
    assert w["route_coords"] == []
    assert w["total_km"] == 0.0


def test_plan_walk_random_never_revisits():
    temples = [temple("A", 0.0, 0.01)]
    w = lib.plan_walk_random((0.0, 0.0), temples, 100.0, fake_leg, random.Random(0))
    assert len(w["stops"]) == 1


def test_plan_walk_random_return_shape():
    temples = [temple("A", 0.0, 0.01)]
    w = lib.plan_walk_random((0.0, 0.0), temples, 10.0, fake_leg, random.Random(0))
    assert set(w.keys()) == {"stops", "route_coords", "segment_breaks", "total_km"}
