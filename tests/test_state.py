from datetime import datetime, timezone

from curfew_x.state import PersistentState


def test_state_round_trip(tmp_path) -> None:
    path = tmp_path / "state.json"
    state = PersistentState(
        enabled=False,
        managed_shutdown=True,
        pardon_until=datetime(2026, 8, 11, tzinfo=timezone.utc).isoformat(),
        force_kill_issued=True,
    )

    state.save(path)

    assert PersistentState.load(path) == state
    assert not path.with_suffix(".json.tmp").exists()

