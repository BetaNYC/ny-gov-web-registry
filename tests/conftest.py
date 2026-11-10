"""Make scripts/ importable and expose shared paths/loaders for the offline test suite."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def load_json(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_network_lookup(monkeypatch):
    """The suite is offline. A run's network record (scripts/network_record.py) would otherwise
    call stat.ripe.net: the one function that reaches the network fails instead, so a run
    records an honest lookup failure. Tests that check the lookup pass their own fake `get`."""
    import network_record

    def offline(url):
        raise OSError("offline test: no lookup made")
    monkeypatch.setattr(network_record, "http_json", offline)
