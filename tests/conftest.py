"""Tests run from a staging dir built by script/test, where the integration
(repository root, HACS content_in_root) is custom_components/voice_assistant_ha."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest_plugins = ["pytest_homeassistant_custom_component"]
