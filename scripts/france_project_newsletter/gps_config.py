"""
Shared config loader for GPS Newsletter pipeline scripts.
Reads config.toml (topology) and .env (secrets) from the repo root.
Import via: sys.path.insert(0, str(pathlib.Path(__file__).parent)); import gps_config
"""
import os
import pathlib
import tomllib

_REPO_ROOT = pathlib.Path(__file__).parents[2]
_CONFIG_PATH = pathlib.Path(__file__).parent / "config.toml"


def load() -> dict:
    """Return merged config: TOML topology + .env secrets as a flat namespace."""
    with open(_CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)

    _load_dotenv()
    return cfg


def llm_url() -> str:
    """LLM server URL. Env var LLAMA_CPP_URL overrides config.toml."""
    _load_dotenv()
    return os.environ.get("LLAMA_CPP_URL") or load()["llm"]["url"]


def changedetection_url() -> str:
    """changedetection.io base URL. Env var CHANGEDETECTION_BASE_URL overrides config.toml."""
    _load_dotenv()
    return os.environ.get("CHANGEDETECTION_BASE_URL") or load()["changedetection"]["url"]


def rsshub_url() -> str:
    """RSSHub bridge base URL. Env var RSSHUB_URL overrides config.toml."""
    _load_dotenv()
    return os.environ.get("RSSHUB_URL") or load()["rsshub"]["base_url"]


def _load_dotenv() -> None:
    env_file = _REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
