from pathlib import Path

import yaml


def test_sunrise_respects_manual_light_state() -> None:
    path = Path(__file__).parents[2] / "plugins/home_assistant_seed/automations.yaml"
    automation = yaml.safe_load(path.read_text())[0]

    light_off = {
        "condition": "state",
        "entity_id": "light.bedroom",
        "state": "off",
    }
    assert automation["condition"] == [light_off]
    assert automation["action"][1]["repeat"]["sequence"][1] == {
        **light_off,
        "state": "on",
    }
