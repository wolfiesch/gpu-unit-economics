from pathlib import Path

from web import deliver_alerts
from web.intelligence_store import IntelligenceStore


def test_run_once_drains_due_queue(tmp_path: Path, monkeypatch) -> None:
    store = IntelligenceStore(tmp_path / "alerts.db")
    monkeypatch.setattr(
        deliver_alerts,
        "deliver_pending",
        lambda provided: {
            "claimed": int(provided is store),
            "delivered": 1,
            "failed": 0,
        },
    )

    assert deliver_alerts.run_once(store) == {
        "claimed": 1,
        "delivered": 1,
        "failed": 0,
    }
