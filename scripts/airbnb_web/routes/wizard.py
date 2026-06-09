from __future__ import annotations

import json
import subprocess
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    session,
)

import cache as cache_mod
import tasks as task_mod
import poi_engine

wizard = Blueprint("wizard", __name__)

TRAVEL_GUIDE_ROOT = Path(__file__).parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

def _fetch_task(
    task: task_mod.TaskState,
    airbnb_url: str,
    gmaps_url: str | None,
    lat: float | None,
    lon: float | None,
    force: bool = False,
) -> None:
    try:
        task_mod.store.update(task.task_id, status=task_mod.Status.RUNNING,
                              progress="Resolving coordinates…", progress_pct=10)

        rlat, rlon, confidence = poi_engine.resolve_coords(airbnb_url, gmaps_url, lat, lon)
        listing_id = poi_engine.listing_id_from_url(airbnb_url)
        cfg        = poi_engine.get_cfg()
        categories = cfg.default_categories if cfg else []

        # Cache look-up (skip on force refresh)
        if not force:
            task_mod.store.update(task.task_id, progress="Checking cache…", progress_pct=18)
            cached = cache_mod.get(listing_id, rlat, rlon, categories,
                                   ttl_days=cfg.cache_ttl_days if cfg else 7)
            if cached:
                # Upgrade legacy cache entries that pre-date status curation
                features = cached.get("geojson", {}).get("features", [])
                if features and "status" not in features[0].get("properties", {}):
                    poi_engine.apply_status_curation(features)
                task_mod.store.update(
                    task.task_id,
                    status=task_mod.Status.DONE,
                    progress="Loaded from cache",
                    progress_pct=100,
                    result={**cached, "from_cache": True},
                )
                return

        def _prog(pct, msg):
            task_mod.store.update(task.task_id, progress=msg, progress_pct=pct)

        task_mod.store.update(task.task_id, progress="Reverse geocoding…", progress_pct=22)

        _filtered, geojson, location, listing_id = poi_engine.fetch_all(
            airbnb_url, rlat, rlon, progress_cb=_prog
        )

        n_pois = len(geojson.get("features", []))
        try:
            listing_title = poi_engine.title_from_airbnb_url(airbnb_url)
        except Exception:
            listing_title = None
        try:
            listing_photo = poi_engine.photo_from_airbnb_url(airbnb_url)
        except Exception:
            listing_photo = None
        result = {
            "lat":           rlat,
            "lon":           rlon,
            "confidence":    confidence,
            "listing_id":    listing_id,
            "location":      location,
            "geojson":       geojson,
            "n_pois":        n_pois,
            "airbnb_url":    airbnb_url,
            "from_cache":    False,
            "listing_title": listing_title,
            "listing_photo": listing_photo,
        }

        # Persist to cache
        cache_mod.put(listing_id, rlat, rlon, categories, result)

        task_mod.store.update(
            task.task_id,
            status=task_mod.Status.DONE,
            progress="Done!",
            progress_pct=100,
            result=result,
        )
    except SystemExit:
        task_mod.store.update(task.task_id, status=task_mod.Status.ERROR,
                              error="Could not extract coordinates — paste the Google Maps URL too.",
                              progress_pct=100)
    except Exception as exc:
        task_mod.store.update(task.task_id, status=task_mod.Status.ERROR,
                              error=str(exc), progress_pct=100)


# ---------------------------------------------------------------------------
# Routes — wizard
# ---------------------------------------------------------------------------

@wizard.get("/")
def index():
    return render_template("index.html")


@wizard.get("/api/listing-preview")
def api_listing_preview():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "url param required"}), 400
    try:
        preview = poi_engine.listing_preview(url)
        return jsonify(preview)
    except Exception:
        return jsonify({"title": None, "photo_url": None})


@wizard.post("/step1/submit")
def step1_submit():
    airbnb_url = request.form.get("airbnb_url", "").strip()
    gmaps_url  = request.form.get("gmaps_url", "").strip() or None
    lat_s      = request.form.get("lat", "").strip() or None
    lon_s      = request.form.get("lon", "").strip() or None
    force      = request.form.get("force_refresh") == "1"

    lat = float(lat_s) if lat_s else None
    lon = float(lon_s) if lon_s else None

    if not airbnb_url:
        return render_template("fragments/error_block.html", error="Please enter an Airbnb URL.")

    # Fast cache check: when lat/lon are explicit (no scraping needed) and no force
    if not force and lat is not None and lon is not None:
        listing_id = poi_engine.listing_id_from_url(airbnb_url)
        cfg        = poi_engine.get_cfg()
        cats       = cfg.default_categories if cfg else []
        cached     = cache_mod.get(listing_id, lat, lon, cats,
                                   ttl_days=cfg.cache_ttl_days if cfg else 7)
        if cached:
            session["fetch_task_id"] = None
            session["airbnb_url"]    = airbnb_url
            session["active_result"] = cached
            listing_title = (cached.get("listing_title")
                             or request.form.get("listing_title") or None)
            listing_photo = (cached.get("listing_photo")
                             or request.form.get("listing_photo") or None)
            return render_template(
                "fragments/step2_map.html",
                task_id=None,
                lat=cached["lat"],
                lon=cached["lon"],
                confidence=cached.get("confidence", "high"),
                listing_id=cached["listing_id"],
                location=cached.get("location", {}),
                geojson_json=json.dumps(cached["geojson"], ensure_ascii=False),
                n_pois=cached["n_pois"],
                airbnb_url=cached["airbnb_url"],
                from_cache=True,
                categories=cfg.categories if cfg else {},
                listing_title=listing_title,
                listing_photo=listing_photo,
            )

    task = task_mod.run_in_thread(_fetch_task, airbnb_url, gmaps_url, lat, lon, force)
    session["fetch_task_id"] = task.task_id
    session["airbnb_url"]    = airbnb_url

    return render_template("fragments/loading_fetch.html",
                           task_id=task.task_id, pct=5, progress="Starting…", error=False)


@wizard.get("/tasks/<task_id>/poll/fetch")
def poll_fetch(task_id: str):
    task = task_mod.store.get(task_id)
    if not task:
        return render_template("fragments/error_block.html", error="Task not found.")

    if task.status == task_mod.Status.ERROR:
        return render_template("fragments/loading_fetch.html",
                               task_id=task_id, pct=100,
                               progress=task.error, error=True)

    if task.status == task_mod.Status.DONE:
        r   = task.result
        cfg = poi_engine.get_cfg()
        session["active_result"] = r
        return render_template(
            "fragments/step2_map.html",
            task_id=task_id,
            lat=r["lat"],
            lon=r["lon"],
            confidence=r["confidence"],
            listing_id=r["listing_id"],
            location=r.get("location", {}),
            geojson_json=json.dumps(r["geojson"], ensure_ascii=False),
            n_pois=r["n_pois"],
            airbnb_url=r["airbnb_url"],
            from_cache=r.get("from_cache", False),
            categories=cfg.categories if cfg else {},
            listing_title=r.get("listing_title"),
            listing_photo=r.get("listing_photo"),
        )

    return render_template("fragments/loading_fetch.html",
                           task_id=task_id,
                           pct=task.progress_pct,
                           progress=task.progress,
                           error=False)


@wizard.post("/step2/continue")
def step2_continue():
    # Prefer the in-memory result from task store; fall back to session copy
    task_id = session.get("fetch_task_id")
    task    = task_mod.store.get(task_id) if task_id else None
    r       = (task.result if task and task.status == task_mod.Status.DONE
               else session.get("active_result"))

    if not r:
        return render_template("fragments/error_block.html",
                               error="Session expired — please start over.")

    active_ids    = set(request.form.getlist("active_ids"))
    secondary_ids = set(request.form.getlist("secondary_ids"))
    geojson       = r["geojson"]
    if active_ids:
        features = [f for f in geojson["features"] if f["id"] in active_ids]
        for f in features:
            f["properties"]["status"] = "secondary" if f["id"] in secondary_ids else "primary"
        geojson = {**geojson, "features": features}

    session["active_geojson"] = geojson
    n_active = len(geojson["features"])

    listing_id    = r["listing_id"]
    location      = r.get("location", {})
    city          = location.get("city") or ""
    neighbourhood = location.get("neighbourhood") or ""
    default_title = f"{neighbourhood}, {city}".strip(", ") or f"Airbnb {listing_id}"
    slug          = f"airbnb/{listing_id}"

    return render_template(
        "fragments/step3_publish.html",
        listing_id=listing_id,
        location=location,
        n_pois=n_active,
        slug=slug,
        default_title=default_title,
        airbnb_url=r["airbnb_url"],
        geojson_json=json.dumps(geojson, ensure_ascii=False),
        has_github_token=bool(current_app.config.get("GITHUB_TOKEN")),
        can_write_local=current_app.config.get("CAN_WRITE_LOCAL", False),
    )


@wizard.post("/step3/create-pr")
def step3_create_pr():
    token   = current_app.config.get("GITHUB_TOKEN", "")
    geojson = session.get("active_geojson")
    r       = _active_result()

    if not token:
        return render_template("fragments/pr_result.html",
                               error="GITHUB_TOKEN not set", pr_url=None)
    if not geojson or not r:
        return render_template("fragments/pr_result.html",
                               error="Session expired — start over", pr_url=None)

    slug        = request.form.get("slug", f"airbnb/{r['listing_id']}")
    title       = request.form.get("title", "")
    description = request.form.get("description", "")

    try:
        pr_url = poi_engine.build_pr(
            token=token,
            slug=slug,
            title=title,
            description=description,
            airbnb_url=r["airbnb_url"],
            lat=r["lat"],
            lon=r["lon"],
            radius=poi_engine.get_cfg().search_radius_m,
            geojson=geojson,
            categories=list(poi_engine.get_cfg().categories.keys()),
            coord_confidence=r["confidence"],
        )
    except Exception as exc:
        return render_template("fragments/pr_result.html", error=str(exc), pr_url=None)

    return render_template("fragments/pr_result.html", pr_url=pr_url, error=None)


@wizard.post("/step3/local-write")
def step3_local_write():
    geojson = session.get("active_geojson")
    r       = _active_result()

    if not geojson or not r:
        return render_template("fragments/local_write_result.html",
                               error="Session expired — start over.", commit=None)

    listing_id  = r["listing_id"]
    slug        = request.form.get("slug", f"airbnb/{listing_id}")
    title       = request.form.get("title", "")
    description = request.form.get("description", "")

    try:
        poi_engine.write_local_hugo_files(
            slug=slug,
            title=title,
            description=description,
            airbnb_url=r["airbnb_url"],
            lat=r["lat"],
            lon=r["lon"],
            geojson=geojson,
            categories=list(poi_engine.get_cfg().categories.keys()),
        )

        add_paths = [f"static/{slug}/", f"content/{slug}/", "data/maps.json"]
        subprocess.run(["git", "add"] + add_paths,
                       cwd=TRAVEL_GUIDE_ROOT, check=True, capture_output=True)
        msg = f"feat({slug}): add neighbourhood map ({r['n_pois']} POIs)"
        subprocess.run(["git", "commit", "-m", msg],
                       cwd=TRAVEL_GUIDE_ROOT, check=True, capture_output=True)
        rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=TRAVEL_GUIDE_ROOT,
        ).decode().strip()
        subprocess.run(["git", "push"],
                       cwd=TRAVEL_GUIDE_ROOT, check=False, capture_output=True)

    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode()
        return render_template("fragments/local_write_result.html",
                               error=err or str(exc), commit=None)
    except Exception as exc:
        return render_template("fragments/local_write_result.html",
                               error=str(exc), commit=None)

    return render_template("fragments/local_write_result.html",
                           commit=rev, error=None, slug=slug)


# ---------------------------------------------------------------------------
# Cache admin routes
# ---------------------------------------------------------------------------

@wizard.get("/cache")
def cache_list():
    return render_template("cache.html", entries=cache_mod.stats())


@wizard.post("/cache/<listing_id>/invalidate")
def cache_invalidate(listing_id: str):
    cache_mod.invalidate(listing_id)
    return render_template("fragments/cache_invalidated.html", listing_id=listing_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _active_result() -> dict | None:
    """Return the current task result from task store or session fallback."""
    task_id = session.get("fetch_task_id")
    task    = task_mod.store.get(task_id) if task_id else None
    if task and task.status == task_mod.Status.DONE:
        return task.result
    return session.get("active_result")
