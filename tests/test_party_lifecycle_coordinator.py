"""Independent runtime workflow tests: no Qt application, widgets or emulator."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pokemon_ev_tracker.core.nuzlocke.party_lifecycle import PartyLifecycleCoordinator


def member(identity="one", hp=20, **fields):
    return SimpleNamespace(
        **{"stable_id": identity, "species": "Shinx", "nickname": "sparky", "level": 12,
           "current_hp": hp, "max_hp": 40, "current_stats": object(), "met_location_id": 1,
           "met_location_name": "Route 202", "met_level": 4, "origin_game": 0,
           "checksum_valid": True, "sample_stale": False, **fields})


def party(*members, **fields):
    return SimpleNamespace(**{"pokemon": members, "party_count": len(members),
                             "party_count_valid": True, "error": None,
                             "live_read_warning": None, **fields})


class Workflow:
    def __init__(self, *, supported=True):
        self.provider = SimpleNamespace(
            capabilities=SimpleNamespace(nuzlocke=supported),
            party_acquisition_candidate=Mock(side_effect=lambda pokemon: SimpleNamespace(
                nickname=pokemon.nickname.upper())),
        )
        self.clock = Mock(return_value=100.0)
        self.coordinator = PartyLifecycleCoordinator(self.provider, clock=self.clock)
        self.events = []
        self.deaths = lambda run, deaths: self.events.append(("death", run, tuple(d.stable_id for d in deaths)))
        self.wipe = lambda wipe: self.events.append(("wipe", wipe.run_id, tuple(m.stable_id for m in wipe.members)))

    def process(self, state, *, frame=10, run="run", session="lua", connected=True,
                received=100.0, payload_missing=False, enabled=False):
        payload = None if payload_missing else SimpleNamespace(
            received_at=received, payload={"frame": frame, "run_id": session})
        self.coordinator.process(
            state, payload, connected=connected, run_id=run, stale_after=5.0,
            prompt_enabled=enabled, on_deaths=self.deaths, on_wipe=self.wipe,
            on_no_run=lambda: self.events.append(("prompt",)),
        )


def test_order_deduplication_nickname_mapping_and_recovery():
    flow = Workflow()
    flow.process(party(member()))
    flow.process(party(member(hp=0)), frame=11)
    assert flow.events == [("death", "run", ("one",)), ("wipe", "run", ("one",))]
    flow.process(party(member(hp=0)), frame=12)
    assert len(flow.events) == 2
    flow.process(party(member()), frame=13)
    flow.process(party(member(hp=0)), frame=14)
    assert len(flow.events) == 4
    assert flow.provider.party_acquisition_candidate.call_count == 5


@pytest.mark.parametrize("failure", [
    "missing-party", "missing-payload", "stale", "disconnect", "checksum", "count",
    "count-flag", "error", "warning", "sample-stale", "missing-stats", "boolean-hp",
    "negative-hp", "oversized-hp", "max-hp", "empty", "restart", "rollback", "new-run",
])
def test_invalid_or_incompatible_sample_rebaselines_without_events(failure):
    flow = Workflow()
    flow.process(party(member()))
    state = party(member(hp=0))
    kwargs = {"frame": 11}
    if failure == "missing-party":
        state = None
    elif failure in {"count", "count-flag", "error", "warning"}:
        state = party(member(hp=0), **{
            "count": {"party_count": 2}, "count-flag": {"party_count_valid": False},
            "error": {"error": "bad RAM"}, "warning": {"live_read_warning": "last good"},
        }[failure])
    elif failure in {"checksum", "sample-stale", "missing-stats", "boolean-hp", "negative-hp", "oversized-hp", "max-hp"}:
        state = party(member(**{
            "checksum": {"hp": 0, "checksum_valid": False},
            "sample-stale": {"hp": 0, "sample_stale": True},
            "missing-stats": {"hp": 0, "current_stats": None},
            "boolean-hp": {"hp": False}, "negative-hp": {"hp": -1},
            "oversized-hp": {"hp": 41}, "max-hp": {"hp": 0, "max_hp": 715},
        }[failure]))
    elif failure == "empty":
        state = party()
    else:
        kwargs.update({
            "missing-payload": {"payload_missing": True}, "stale": {"received": 94.9},
            "disconnect": {"connected": False}, "restart": {"session": "new"},
            "rollback": {"frame": 1}, "new-run": {"run": "new"},
        }[failure])
    flow.process(state, **kwargs)
    assert flow.events == []
    flow.process(party(member(hp=0)), frame=12)
    assert flow.events == []


def test_reorder_is_identity_based_and_partial_faint_is_not_a_wipe():
    flow = Workflow()
    flow.process(party(member("one"), member("two")))
    flow.process(party(member("two"), member("one", hp=0)), frame=11)
    assert flow.events == [("death", "run", ("one",))]


def test_run_end_from_wipe_handler_resets_and_suppresses_prompt():
    flow = Workflow()
    flow.process(party(member()))

    def end(wipe):
        flow.events.append(("wipe", wipe.run_id))
        flow.coordinator.run_ended()

    flow.wipe = end
    flow.process(party(member(hp=0)), frame=11, enabled=True)
    flow.process(party(member(hp=0)), frame=12, run="new")
    for frame in range(13, 19):
        flow.process(party(member()), frame=frame, run=None, enabled=True)
    assert [event[0] for event in flow.events] == ["death", "wipe"]


def test_prompt_progress_enabled_active_run_and_missing_frames():
    flow = Workflow()
    for frame in (10, 10, None, 11, 12, 13):
        flow.process(party(member()), frame=frame, run=None, enabled=True)
    assert flow.events == []
    flow.process(party(member()), frame=14, run=None, enabled=True)
    assert flow.events == [("prompt",)]
    for frame in (15, 16, 17, 18):
        flow.process(party(member()), frame=frame, run=None, enabled=True)
    assert flow.events == [("prompt",)]
    disabled = Workflow()
    active = Workflow()
    for frame in range(10, 16):
        disabled.process(party(member()), frame=frame, run=None)
        active.process(party(member()), frame=frame, enabled=True)
    assert disabled.events == active.events == []


def test_unsupported_provider_uses_existing_nickname_without_conversion():
    flow = Workflow(supported=False)
    flow.process(party(member()), run=None)
    flow.process(party(member(hp=0)), frame=11, run=None)
    assert not flow.provider.party_acquisition_candidate.called
    assert flow.events == [("death", None, ("one",))]


def test_handler_failure_propagates_and_does_not_run_later_handlers():
    flow = Workflow()
    flow.process(party(member()))
    flow.deaths = Mock(side_effect=OSError("persistence failed"))
    flow.wipe = Mock()
    with pytest.raises(OSError, match="persistence failed"):
        flow.process(party(member(hp=0)), frame=11)
    assert not flow.wipe.called
    assert flow.events == []


def test_validation_clock_is_not_read_for_missing_or_invalid_party():
    flow = Workflow()
    flow.process(None)
    flow.process(party(member(), party_count_valid=False))
    flow.process(party(member()), payload_missing=True)
    assert not flow.clock.called
    flow.process(party(member()))
    flow.clock.assert_called_once_with()
