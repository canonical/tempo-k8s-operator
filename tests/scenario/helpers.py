from pathlib import Path

import scenario
import yaml

from tempo import Tempo


def get_tempo_config(container: scenario.Container, context: scenario.Context):
    fs = container.get_filesystem(context)
    cfg_path = Path(str(fs) + Tempo.config_path)
    return yaml.safe_load(cfg_path.read_text())
