"""Existing raw-party validation contracts shared by runtime observers."""

from collections.abc import Callable


def valid_acquisition_snapshot(party_state) -> bool:
    if (
        party_state is None
        or not party_state.party_count_valid
        or party_state.error
        or getattr(party_state, "live_read_warning", None)
        or party_state.party_count != len(party_state.pokemon)
    ):
        return False
    return all(
        pokemon.checksum_valid and not getattr(pokemon, "sample_stale", False)
        for pokemon in party_state.pokemon
    )


def valid_death_snapshot(
    party_state, party_payload, stale_after: float, *, clock: Callable[[], float]
) -> bool:
    if not valid_acquisition_snapshot(party_state) or party_payload is None:
        return False
    received_at = getattr(party_payload, "received_at", None)
    if not isinstance(received_at, (int, float)) or clock() - received_at > stale_after:
        return False
    return all(
        getattr(pokemon, "current_stats", None) is not None
        and isinstance(getattr(pokemon, "current_hp", None), int)
        and not isinstance(getattr(pokemon, "current_hp", None), bool)
        and isinstance(getattr(pokemon, "max_hp", None), int)
        and 1 <= pokemon.max_hp <= 714
        and 0 <= pokemon.current_hp <= pokemon.max_hp
        for pokemon in party_state.pokemon
    )


def frame_number(payload) -> int | None:
    value = payload.get("frame") if isinstance(payload, dict) else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
