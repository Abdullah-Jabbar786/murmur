"""Configuration loader for Murmur.

Loads config/default.json and optionally applies file-based or runtime overrides.
Uses standard JSON for full Python 3.10+ compatibility without extra dependencies.
"""

import json
import os
from typing import Any, Dict, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG_PATH = os.path.join(_BASE_DIR, "config", "default.json")


def load_config(
    path: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Load configuration dictionary.

    Args:
        path: Path to a JSON config file. If None, loads config/default.json.
        overrides: Dictionary of values to overwrite in the loaded config.

    Returns:
        Consolidated configuration dict.
    """
    config_file = path if path is not None else _DEFAULT_CONFIG_PATH
    config: Dict[str, Any] = {}

    if os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)

    if overrides:
        config.update(overrides)

    return config
