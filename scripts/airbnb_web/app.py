#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["flask>=3.0", "python-dotenv", "requests",
#                 "beautifulsoup4", "overpass", "rich", "staticmap"]
# ///
"""
Airbnb Neighbourhood Map Web App

Usage (dev):
    uv run scripts/airbnb_web/app.py

Usage (prod, via gunicorn):
    uv run gunicorn -k gthread --threads 4 --workers 1 \
        --bind 127.0.0.1:5010 \
        'app:create_app()'
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys
from pathlib import Path

# Ensure this directory is on sys.path so Flask can find blueprints / modules
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from flask import Flask

TRAVEL_GUIDE_ROOT = _HERE.parent.parent


def _in_git_repo() -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=TRAVEL_GUIDE_ROOT,
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _can_write_local() -> bool:
    try:
        r = subprocess.run(
            ["git", "status", "--short"],
            cwd=TRAVEL_GUIDE_ROOT,
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _read_commit_info():
    try:
        h = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=TRAVEL_GUIDE_ROOT
        ).decode().strip()
        return h
    except Exception:
        return ""


def _configure_logging(root: Path) -> None:
    log_file = os.environ.get("LOG_FILE") or str(root / "logs" / "airbnb_web.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG if os.environ.get("FLASK_DEBUG") else logging.INFO)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)


def create_app(config: dict | None = None) -> Flask:
    import poi_engine

    _configure_logging(TRAVEL_GUIDE_ROOT)

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    poi_engine.initialize(env_path=_HERE / ".env")

    app.config["GITHUB_TOKEN"]     = os.environ.get("GITHUB_TOKEN", "")
    app.config["CAN_WRITE_LOCAL"]  = _can_write_local()
    app.config["IN_GIT_REPO"]      = _in_git_repo()

    commit = _read_commit_info()

    @app.context_processor
    def _ctx():
        return {
            "has_github_token": bool(app.config["GITHUB_TOKEN"]),
            "can_write_local":  app.config["CAN_WRITE_LOCAL"],
            "in_git_repo":      app.config["IN_GIT_REPO"],
            "commit_short":     commit[:7] if commit else "",
            "categories":       poi_engine.get_cfg().categories if poi_engine.get_cfg() else {},
        }

    from routes.wizard import wizard
    app.register_blueprint(wizard)

    return app


def run_dev() -> None:
    app = create_app()
    app.run(host="127.0.0.1", port=5010, threaded=True)


def run_prod() -> None:
    try:
        from gunicorn.app.base import BaseApplication

        class _App(BaseApplication):
            def __init__(self, application, options=None):
                self.options     = options or {}
                self.application = application
                super().__init__()

            def load_config(self):
                for k, v in self.options.items():
                    if k in self.cfg.settings and v is not None:
                        self.cfg.set(k.lower(), v)

            def load(self):
                return self.application

        _App(create_app(), {
            "bind":         "127.0.0.1:5010",
            "workers":      1,
            "worker_class": "gthread",
            "threads":      4,
            "loglevel":     "info",
        }).run()
    except ImportError:
        print("gunicorn not installed — falling back to Flask dev server")
        run_dev()


if __name__ == "__main__":
    run_dev()
