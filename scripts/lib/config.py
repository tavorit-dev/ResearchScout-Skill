"""Configuration loading with spec defaults."""
from pathlib import Path

import yaml

DEFAULTS = {
    "max_rounds": 8,
    "max_branch_depth": 3,
    "max_active_branches": 5,
    "max_documents_per_round": 50,
    "max_documents_per_research": 200,
    "max_requests_per_research": 1000,
    "max_runtime_minutes": 60,
    "min_new_information_ratio": 0.10,
    "min_action_priority": 60,
    "stop_when_no_progress_rounds": 2,
}

ACTION_TIME_WINDOWS = {
    "deepen": 180,
    "broaden": 180,
    "verify": None,
    "backtrack": 1825,
}

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def load_config(config_dir=None):
    """Load config/*.yaml and merge spec defaults underneath."""
    directory = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    merged = {"iteration_policy": dict(DEFAULTS)}
    if directory.is_dir():
        for path in sorted(directory.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for key, value in data.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key].update(value)
                else:
                    merged[key] = value
    return merged
