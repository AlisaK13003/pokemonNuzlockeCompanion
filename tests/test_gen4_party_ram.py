from __future__ import annotations

from types import SimpleNamespace

import pytest

from pokemon_ev_tracker.data_sources.bizhawk import BizHawkRamDataSource
from pokemon_ev_tracker.games.platinum.decoder import decode_party, nickname_is_default
from pokemon_ev_tracker.games.platinum.memory import PLATINUM_US
from pokemon_ev_tracker.games.platinum.pc_storage import (
    PC_BOX_RECORDS_SIZE,
    PC_BOXES_PAGE_ID,
    PC_BOXES_PAGE_SIZE,
    decode_pc_storage_payload,
)
from pokemon_ev_tracker.pokemon.gen4.crypto import (
    BOX_DATA_SIZE,
    block_order,
    calculate_checksum,
    decrypt_box_data,
    encrypt_box_data,
    prng,
    shuffle_index,
    unshuffle_blocks,
    xor_words,
)
from pokemon_ev_tracker.pokemon.gen4.ivs import decode_individual_values
from pokemon_ev_tracker.pokemon.gen4.nature import nature_from_pid
from pokemon_ev_tracker.pokemon.gen4.structure import (
    FRIENDSHIP_BOX_DATA_OFFSET,
    FRIENDSHIP_RECORD_OFFSET,
    HELD_ITEM_BOX_DATA_OFFSET,
    HELD_ITEM_RECORD_OFFSET,
    PARTY_POKEMON_SIZE,
    decode_box_pokemon,
    decode_party_pokemon,
)
from pokemon_ev_tracker.ui.party_layout import party_card_positions


def test_prng_returns_upper_16_bits_after_lcg_step() -> None:
    rng = prng(0)

    assert next(rng) == 0
    assert next(rng) == 0xE97E


def test_shuffle_permutation_selection() -> None:
    assert shuffle_index(0x00000000) == 0
    assert block_order(0x00000000) == "ABCD"
    assert shuffle_index(0x00002000) == 1
    assert block_order(0x00002000) == "ABDC"


def test_block_unshuffle_restores_original_order() -> None:
    original = b"A" * 32 + b"B" * 32 + b"C" * 32 + b"D" * 32
    shuffled = b"A" * 32 + b"C" * 32 + b"D" * 32 + b"B" * 32

    assert block_order(0x00006000) == "ACDB"
    assert unshuffle_blocks(shuffled, 0x00006000) == original


def test_checksum_calculation_uses_16_bit_words() -> None:
    data = bytearray(BOX_DATA_SIZE)
    data[0:2] = (1).to_bytes(2, "little")
    data[2:4] = (0xFFFF).to_bytes(2, "little")
    data[4:6] = (2).to_bytes(2, "little")

    assert calculate_checksum(bytes(data)) == 2


def test_decrypt_and_extract_evs_for_party_pokemon() -> None:
    record = _party_record(
        pid=0x12345678,
        species_id=253,
        evs=(0, 32, 0, 28, 0, 4),
        level=18,
        current_hp=44,
    )

    decoded = decode_party_pokemon(record, address=0x0210EDC4)

    assert decoded.diagnostics.checksum_valid is True
    assert decoded.species_id == 253
    assert decoded.evs.hp == 0
    assert decoded.evs.attack == 32
    assert decoded.evs.defense == 0
    assert decoded.evs.special_attack == 0
    assert decoded.evs.special_defense == 4
    assert decoded.evs.speed == 28
    assert decoded.evs.total == 64
    assert decoded.level == 18
    assert decoded.current_hp == 44


def test_gen4_move_slots_decode_and_resolve_to_platinum_names() -> None:
    record = _party_record(
        pid=0x12345678,
        species_id=179,
        evs=(0, 0, 0, 0, 0, 0),
        moves=(98, 45, 0, 1),
        move_pps=(17, 29, 0, 34),
        move_pp_ups=(1, 2, 0, 3),
    )

    decoded = decode_party_pokemon(record)
    party = decode_party((1).to_bytes(4, "little") + record + bytes(PARTY_POKEMON_SIZE * 5))

    assert decoded.diagnostics.checksum_valid
    assert decoded.move_ids == (98, 45, 0, 1)
    assert decoded.move_current_pps == (17, 29, 0, 34)
    assert decoded.move_pp_ups == (1, 2, 0, 3)
    assert party.pokemon[0].moves == ("Quick Attack", "Growl", "Pound")

    updated = decode_party_pokemon(_party_record(
        pid=0x12345678, species_id=179, evs=(0, 0, 0, 0, 0, 0),
        moves=(98, 85, 0, 1), move_pps=(16, 10, 0, 34),
        move_pp_ups=(1, 0, 0, 3),
    ))
    assert updated.move_ids == (98, 85, 0, 1)
    assert updated.move_current_pps == (16, 10, 0, 34)
    assert updated.move_pp_ups == (1, 0, 0, 3)


@pytest.mark.parametrize("friendship", (0, 164, 255))
def test_gen4_friendship_decodes_unsigned_byte_from_block_a(friendship: int) -> None:
    record = _party_record(
        pid=0x12345678,
        species_id=179,
        evs=(0, 0, 0, 0, 0, 0),
        friendship=friendship,
    )

    decoded = decode_party_pokemon(record)
    party = decode_party(
        (1).to_bytes(4, "little") + record + bytes(PARTY_POKEMON_SIZE * 5)
    )

    assert decoded.diagnostics.checksum_valid
    assert decoded.friendship == friendship
    assert party.pokemon[0].friendship == friendship
    assert FRIENDSHIP_RECORD_OFFSET == 0x14
    assert FRIENDSHIP_BOX_DATA_OFFSET == 0x0C


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (0, "Very Low"),
        (49, "Very Low"),
        (50, "Low"),
        (99, "Low"),
        (100, "Neutral"),
        (149, "Neutral"),
        (150, "High"),
        (199, "High"),
        (200, "Very High"),
        (254, "Very High"),
        (255, "Max"),
    ),
)
def test_friendship_label_boundaries(value: int, expected: str) -> None:
    from pokemon_ev_tracker.pokemon.gen4.friendship import friendship_label

    assert friendship_label(value) == expected


def test_pokemon_stable_identity_ignores_evolution_level_evs_item_and_nickname() -> None:
    original = decode_party_pokemon(
        _party_record(
            pid=0x12345678,
            species_id=393,
            evs=(0, 0, 0, 0, 0, 0),
            level=5,
            nickname="piplup",
            held_item_id=0,
        )
    )
    evolved = decode_party_pokemon(
        _party_record(
            pid=0x12345678,
            species_id=394,
            evs=(4, 12, 0, 0, 0, 8),
            level=18,
            nickname="plip",
            held_item_id=112,
        )
    )

    assert original.stable_id == evolved.stable_id


def test_iv_bitfield_decoding_keeps_gen4_flags_separate() -> None:
    packed = (
        31
        | (0 << 5)
        | (17 << 10)
        | (14 << 15)
        | (8 << 20)
        | (24 << 25)
        | (1 << 30)
        | (1 << 31)
    )

    ivs = decode_individual_values(packed)

    assert (ivs.hp, ivs.attack, ivs.defense) == (31, 0, 17)
    assert (ivs.speed, ivs.special_attack, ivs.special_defense) == (14, 8, 24)
    assert ivs.is_egg
    assert ivs.has_nickname
    assert all(0 <= value <= 31 for value in (ivs.hp, ivs.attack, ivs.defense,
                                               ivs.speed, ivs.special_attack,
                                               ivs.special_defense))


def test_party_model_exposes_ivs_in_display_stat_order() -> None:
    record = _party_record(
        pid=0x12345678,
        species_id=183,
        evs=(0, 0, 0, 0, 0, 0),
        ivs=(31, 0, 17, 14, 8, 24),
        is_egg=True,
        has_nickname=True,
    )
    raw_party = (1).to_bytes(4, "little") + record + bytes(PARTY_POKEMON_SIZE * 5)

    pokemon = decode_party(raw_party).pokemon[0]

    assert (pokemon.hp_iv, pokemon.attack_iv, pokemon.defense_iv) == (31, 0, 17)
    assert (pokemon.speed_iv, pokemon.special_attack_iv, pokemon.special_defense_iv) == (
        14,
        8,
        24,
    )
    assert pokemon.decoded.ivs.is_egg
    assert pokemon.decoded.ivs.has_nickname


def test_nature_comes_from_pid_modulo_25_and_neutral_natures_have_no_modifiers() -> None:
    adamant = nature_from_pid(0x12345678 - (0x12345678 % 25) + 3)
    timid = nature_from_pid(10)
    neutral = nature_from_pid(12)

    assert (adamant.id, adamant.name) == (3, "Adamant")
    assert (adamant.increased_stat, adamant.decreased_stat) == (
        "attack",
        "special_attack",
    )
    assert (timid.increased_stat, timid.decreased_stat) == ("speed", "attack")
    assert neutral.name == "Serious"
    assert neutral.increased_stat is None
    assert neutral.decreased_stat is None


def test_platinum_ability_lookup_uses_actual_ability_id_and_species_slot() -> None:
    first = decode_party_pokemon(
        _party_record(
            pid=0x12345678,
            species_id=183,
            evs=(0, 0, 0, 0, 0, 0),
            ability_id=47,
        )
    )
    second = decode_party_pokemon(
        _party_record(
            pid=0x12345679,
            species_id=183,
            evs=(0, 0, 0, 0, 0, 0),
            ability_id=37,
        )
    )
    party_bytes = (2).to_bytes(4, "little") + _party_record(
        pid=0x12345678,
        species_id=183,
        evs=(0, 0, 0, 0, 0, 0),
        ability_id=47,
    ) + _party_record(
        pid=0x12345679,
        species_id=183,
        evs=(0, 0, 0, 0, 0, 0),
        ability_id=37,
    ) + bytes(PARTY_POKEMON_SIZE * 4)
    party = decode_party(party_bytes)

    assert first.ability_id == 47
    assert second.ability_id == 37
    assert [(mon.ability_slot, mon.ability_name) for mon in party.pokemon] == [
        (1, "Thick Fat"),
        (2, "Huge Power"),
    ]


def test_actual_party_stats_are_extracted_from_encrypted_tail() -> None:
    decoded = decode_party_pokemon(
        _party_record(
            pid=0x12345678,
            species_id=183,
            evs=(0, 0, 0, 0, 0, 0),
            max_hp=53,
            attack_stat=27,
            defense_stat=30,
            speed_stat=22,
            special_attack_stat=18,
            special_defense_stat=29,
        )
    )

    assert decoded.current_stats.max_hp == 53
    assert decoded.current_stats.attack == 27
    assert decoded.current_stats.defense == 30
    assert decoded.current_stats.special_attack == 18
    assert decoded.current_stats.special_defense == 29
    assert decoded.current_stats.speed == 22


def test_party_decode_maps_species_and_empty_slots() -> None:
    record = _party_record(pid=0x12345678, species_id=387, evs=(1, 2, 3, 4, 5, 6))
    raw_party = (1).to_bytes(4, "little") + record + bytes(PARTY_POKEMON_SIZE * 5)

    party = decode_party(raw_party)

    assert party.party_count == 1
    assert party.party_count_valid is True
    assert len(party.pokemon) == 1
    assert party.pokemon[0].slot == 1
    assert party.pokemon[0].species == "Turtwig"
    assert party.pokemon[0].evs["special_attack"] == 5
    assert party.pokemon[0].evs["special_defense"] == 6
    assert party.pokemon[0].evs["speed"] == 4
    assert party.pokemon[0].nickname == "Turtwig"
    assert party.pokemon[0].held_item_id == 0
    assert party.pokemon[0].held_item_name is None


def test_party_decode_exposes_held_item_id_and_name() -> None:
    record = _party_record(
        pid=0x12345678,
        species_id=179,
        evs=(0, 0, 0, 0, 0, 0),
        held_item_id=293,
    )
    raw_party = (1).to_bytes(4, "little") + record + bytes(PARTY_POKEMON_SIZE * 5)

    pokemon = decode_party(raw_party).pokemon[0]

    assert pokemon.held_item_id == 293
    assert pokemon.held_item_name == "Power Anklet"
    assert HELD_ITEM_BOX_DATA_OFFSET == 0x02
    assert HELD_ITEM_RECORD_OFFSET == 0x0A


def test_gen4_nickname_decodes_custom_name_and_preserves_case() -> None:
    decoded = decode_party_pokemon(
        _party_record(
            pid=0x12345678,
            species_id=21,
            evs=(0, 0, 0, 0, 0, 0),
            nickname="sheddy",
        )
    )

    assert decoded.nickname == "sheddy"


def test_gen4_nickname_decodes_to_the_full_field_limit_without_terminator() -> None:
    decoded = decode_party_pokemon(
        _party_record(
            pid=0x12345678,
            species_id=25,
            evs=(0, 0, 0, 0, 0, 0),
            nickname="ABCDEFGHIJK",
            nickname_terminated=False,
        )
    )

    assert decoded.nickname == "ABCDEFGHIJK"
    assert decoded.nickname_diagnostics.terminator_unit_index is None


def test_gen4_nickname_preserves_spaces_and_decodes_accented_glyphs() -> None:
    decoded = decode_party_pokemon(
        _party_record(
            pid=0x12345678,
            species_id=25,
            evs=(0, 0, 0, 0, 0, 0),
            nickname="  Poké!  ",
        )
    )

    assert decoded.nickname == "  Poké!  "


def test_default_species_name_detection_ignores_case_and_fullwidth_glyphs() -> None:
    assert nickname_is_default("SPEAROW", "Spearow")
    assert nickname_is_default("Ｓｐｅａｒｏｗ", "Spearow")
    assert not nickname_is_default("sheddy", "Spearow")


def test_default_species_nickname_remains_separate_from_species() -> None:
    record = _party_record(
        pid=0x12345678,
        species_id=21,
        evs=(0, 0, 0, 0, 0, 0),
        nickname="SPEAROW",
    )
    party = decode_party((1).to_bytes(4, "little") + record + bytes(PARTY_POKEMON_SIZE * 5))
    pokemon = party.pokemon[0]

    assert pokemon.nickname == "SPEAROW"
    assert pokemon.species == "Spearow"
    assert nickname_is_default(pokemon.nickname, pokemon.species)


def test_malformed_nickname_data_falls_back_to_species_name() -> None:
    record = _party_record(
        pid=0x12345678,
        species_id=387,
        evs=(0, 0, 0, 0, 0, 0),
        nickname_codes=(0x0300,),
    )
    decoded = decode_party_pokemon(record)
    party = decode_party((1).to_bytes(4, "little") + record + bytes(PARTY_POKEMON_SIZE * 5))

    assert decoded.nickname is None
    assert party.pokemon[0].species == "Turtwig"
    assert party.pokemon[0].nickname == "Turtwig"


def test_party_card_layout_supports_one_through_six_members() -> None:
    expected_positions = {
        1: ((0, 0),),
        2: ((0, 0), (0, 1)),
        3: ((0, 0), (0, 1), (0, 2)),
        4: ((0, 0), (0, 1), (0, 2), (1, 0)),
        5: ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1)),
        6: ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)),
    }

    for count, positions in expected_positions.items():
        assert party_card_positions(count) == positions


def test_invalid_checksum_is_reported_and_evs_not_marked_valid() -> None:
    record = bytearray(_party_record(pid=0x12345678, species_id=253, evs=(0, 1, 2, 3, 4, 5)))
    record[0x20] ^= 0xFF

    decoded = decode_party_pokemon(bytes(record))

    assert decoded.diagnostics.checksum_valid is False


def test_battle_stats_are_validated_separately_from_pokemon_checksum() -> None:
    record = _party_record(
        pid=0x12345678,
        species_id=16,
        evs=(0, 0, 0, 0, 0, 0),
        level=4,
        current_hp=53720,
        max_hp=43591,
    )

    decoded = decode_party_pokemon(record)

    assert decoded.diagnostics.checksum_valid
    assert not decoded.diagnostics.battle_stats_valid
    assert "Max HP 43591" in decoded.diagnostics.battle_stats_error
    assert decoded.level == 4
    assert decoded.current_hp == 53720
    assert decoded.max_hp == 43591
    assert len(decoded.diagnostics.battle_stats_raw_hex.split()) == 20
    assert len(decoded.diagnostics.battle_stats_decrypted_hex.split()) == 20


def test_impossible_party_count_stops_decoding() -> None:
    raw_party = (7).to_bytes(4, "little") + bytes(PARTY_POKEMON_SIZE * 6)

    party = decode_party(raw_party)

    assert party.party_count == 7
    assert party.party_count_valid is False
    assert party.pokemon == ()


def test_bizhawk_data_source_prefers_scanned_checksum_valid_party_candidate() -> None:
    first = _party_record(pid=0x12345678, species_id=287, evs=(0, 1, 2, 3, 4, 5))
    second = _party_record(pid=0x87654321, species_id=21, evs=(6, 7, 8, 9, 10, 11))
    raw_candidate = (2).to_bytes(4, "little") + first + second + bytes(PARTY_POKEMON_SIZE * 4)
    raw_primary = (0).to_bytes(4, "little") + bytes(PARTY_POKEMON_SIZE * 6)
    payload = SimpleNamespace(
        payload={
            "party_count": 0,
            "party_count_valid": True,
            "party_address": "0x0227E1C4",
            "raw_party_hex": raw_primary.hex(),
            "party_candidates": [
                {
                    "relative_offset": "0xD120",
                    "address": "0x0227E25C",
                    "party_count": 2,
                    "raw_party_hex": raw_candidate.hex(),
                }
            ],
        }
    )

    party = BizHawkRamDataSource()._decode_party_payload(payload)

    assert party is not None
    assert party.party_count == 2
    assert party.candidate_count == 1
    assert [pokemon.species for pokemon in party.pokemon] == ["Slakoth", "Spearow"]
    assert all(pokemon.checksum_valid for pokemon in party.pokemon)


def test_bizhawk_display_holds_last_valid_slot_during_bad_ram_sample() -> None:
    source = BizHawkRamDataSource()
    payload = SimpleNamespace(
        payload={"run_id": "test-run", "core": "NDS", "domain": "Main RAM"}
    )
    valid_raw = (1).to_bytes(4, "little") + _party_record(
        pid=0x12345678, species_id=179, evs=(1, 2, 3, 4, 5, 6), nickname="sheepy"
    ) + bytes(PARTY_POKEMON_SIZE * 5)
    valid_state = decode_party(valid_raw)

    initial_display = source._stabilize_display_party(valid_state, payload)

    assert initial_display is not None
    assert initial_display.pokemon[0].species == "Mareep"
    assert not initial_display.pokemon[0].sample_stale

    invalid_record = bytearray(
        _party_record(
            pid=0x12345678,
            species_id=500,
            evs=(100, 100, 100, 100, 100, 100),
            nickname="glitch",
        )
    )
    invalid_record[0x08] ^= 0x01
    invalid_raw = (1).to_bytes(4, "little") + invalid_record + bytes(PARTY_POKEMON_SIZE * 5)
    invalid_state = decode_party(invalid_raw)
    display_state = source._stabilize_display_party(invalid_state, payload)

    assert not invalid_state.pokemon[0].checksum_valid
    assert display_state is not None
    assert display_state.pokemon[0].species == "Mareep"
    assert display_state.pokemon[0].nickname == "sheepy"
    assert display_state.pokemon[0].decoded.diagnostics.pid == 0x12345678
    assert display_state.pokemon[0].sample_stale
    assert "slot(s) 1" in display_state.live_read_warning


def test_bizhawk_display_holds_sane_battle_stats_for_same_pid() -> None:
    source = BizHawkRamDataSource()
    payload = SimpleNamespace(payload={"run_id": "test-run"})
    valid_state = decode_party(
        (1).to_bytes(4, "little")
        + _party_record(
            pid=0x12345678,
            species_id=16,
            evs=(0, 0, 0, 0, 0, 0),
            level=6,
            current_hp=21,
            max_hp=21,
        )
        + bytes(PARTY_POKEMON_SIZE * 5)
    )
    source._stabilize_display_party(valid_state, payload)
    invalid_stats_state = decode_party(
        (1).to_bytes(4, "little")
        + _party_record(
            pid=0x12345678,
            species_id=16,
            evs=(0, 0, 0, 0, 0, 0),
            level=4,
            current_hp=53720,
            max_hp=43591,
        )
        + bytes(PARTY_POKEMON_SIZE * 5)
    )

    display_state = source._stabilize_display_party(invalid_stats_state, payload)

    assert invalid_stats_state.pokemon[0].checksum_valid
    assert not invalid_stats_state.pokemon[0].decoded.diagnostics.battle_stats_valid
    assert display_state is not None
    pokemon = display_state.pokemon[0]
    assert pokemon.species == "Pidgey"
    assert pokemon.level == 6
    assert pokemon.current_hp == 21
    assert pokemon.max_hp == 21
    assert pokemon.battle_stats_stale
    assert not pokemon.sample_stale
    assert "last sane level/HP" in display_state.live_read_warning


def test_bizhawk_display_withholds_bad_battle_stats_without_matching_pid_cache() -> None:
    source = BizHawkRamDataSource()
    payload = SimpleNamespace(payload={"run_id": "test-run"})
    invalid_stats_state = decode_party(
        (1).to_bytes(4, "little")
        + _party_record(
            pid=0x12345678,
            species_id=16,
            evs=(0, 0, 0, 0, 0, 0),
            level=4,
            current_hp=53720,
            max_hp=43591,
        )
        + bytes(PARTY_POKEMON_SIZE * 5)
    )

    display_state = source._stabilize_display_party(invalid_stats_state, payload)

    assert display_state is not None
    assert display_state.pokemon[0].level is None
    assert display_state.pokemon[0].current_hp is None
    assert display_state.pokemon[0].max_hp is None
    assert display_state.pokemon[0].battle_stats_stale
    assert "level/HP withheld" in display_state.live_read_warning


def test_bizhawk_display_omits_unverified_slot_until_valid_sample_arrives() -> None:
    source = BizHawkRamDataSource()
    payload = SimpleNamespace(payload={"run_id": "test-run"})
    invalid_record = bytearray(
        _party_record(pid=1, species_id=500, evs=(1, 2, 3, 4, 5, 6))
    )
    invalid_record[0x08] ^= 0x01
    invalid_state = decode_party(
        (1).to_bytes(4, "little") + invalid_record + bytes(PARTY_POKEMON_SIZE * 5)
    )

    display_state = source._stabilize_display_party(invalid_state, payload)

    assert display_state is not None
    assert display_state.pokemon == ()
    assert display_state.live_read_warning == "Waiting for checksum-valid RAM data in slot(s) 1."


def test_bizhawk_display_accepts_recovered_valid_sample() -> None:
    source = BizHawkRamDataSource()
    payload = SimpleNamespace(payload={"run_id": "test-run"})
    valid_state = decode_party(
        (1).to_bytes(4, "little")
        + _party_record(pid=1, species_id=179, evs=(0, 0, 0, 0, 0, 0))
        + bytes(PARTY_POKEMON_SIZE * 5)
    )
    source._stabilize_display_party(valid_state, payload)
    recovered_state = decode_party(
        (1).to_bytes(4, "little")
        + _party_record(pid=2, species_id=443, evs=(1, 0, 0, 0, 0, 0))
        + bytes(PARTY_POKEMON_SIZE * 5)
    )

    display_state = source._stabilize_display_party(recovered_state, payload)

    assert display_state is not None
    assert display_state.pokemon[0].species == "Gible"
    assert display_state.pokemon[0].decoded.diagnostics.pid == 2
    assert not display_state.pokemon[0].sample_stale
    assert display_state.live_read_warning is None


def test_platinum_main_ram_translation_documents_bizhawk_domain_offset() -> None:
    assert PLATINUM_US.main_ram_offset(0x02101D2C) == 0x00101D2C
    assert PLATINUM_US.player_party_relative_offset == 0xD088
    assert 0xD094 in PLATINUM_US.player_party_relative_offset_candidates


def test_encrypt_decrypt_roundtrip() -> None:
    pid = 0x12345678
    plain = _plain_box(species_id=253, evs=(0, 32, 0, 28, 0, 4))
    checksum = calculate_checksum(plain)

    encrypted = encrypt_box_data(plain, pid, checksum)

    assert decrypt_box_data(encrypted, pid, checksum) == plain


def _boxed_fixture(
    *, pid: int = 0x12345678, species_id: int = 399, nickname: str = "beaver"
) -> bytes:
    party = _party_record(
        pid=pid,
        species_id=species_id,
        evs=(0, 0, 0, 0, 0, 0),
        nickname=nickname,
    )
    plain = bytearray(decrypt_box_data(party[0x08:0x88], pid, int.from_bytes(party[6:8], "little")))
    plain[0x3E:0x40] = (0x12).to_bytes(2, "little")
    plain[0x7C] = 6
    checksum = calculate_checksum(bytes(plain))
    return party[:6] + checksum.to_bytes(2, "little") + encrypt_box_data(bytes(plain), pid, checksum)


def _pc_storage_scan_payload(frame: int, records: tuple[tuple[int, int, bytes], ...]) -> dict:
    save_body_address = 0x022711C8
    pc_boxes_address = save_body_address + 0x8000
    records_address = pc_boxes_address + 4
    return {
        "type": "pc_storage",
        "run_id": "test-run",
        "frame": frame,
        "scan_ok": True,
        "save_data_pointer": hex(save_body_address),
        "save_data_body_address": hex(save_body_address),
        "save_data_struct_address_inferred": hex(save_body_address - 0x14),
        "page_info_address": hex(save_body_address + 0x20010 + PC_BOXES_PAGE_ID * 0x10),
        "page_id": PC_BOXES_PAGE_ID,
        "page_block_id": 1,
        "page_location": "0x8000",
        "page_size": hex(PC_BOXES_PAGE_SIZE),
        "pc_boxes_address": hex(pc_boxes_address),
        "records_address": hex(records_address),
        "records_offset": hex(records_address - 0x02000000),
        "final_address_valid": True,
        "box_count": 18,
        "slots_per_box": 30,
        "record_stride": 136,
        "records_scanned": 540,
        "bytes_read": PC_BOX_RECORDS_SIZE,
        "scan_duration_ms": 3,
        "records": [
            {
                "box": box,
                "slot": slot,
                "address": hex(records_address + ((box - 1) * 30 + slot - 1) * 136),
                "raw_hex": raw.hex(),
            }
            for box, slot, raw in records
        ],
    }


def test_box_pk4_decoder_reuses_gen4_checksum_nickname_and_encounter_fields() -> None:
    record = _boxed_fixture()

    decoded = decode_box_pokemon(record, address=0x02290004)

    assert len(record) == 136
    assert decoded.checksum_valid
    assert decoded.species_id == 399
    assert decoded.nickname == "beaver"
    assert decoded.met_location_id == 0x12
    assert decoded.met_level == 6
    assert decoded.pid == 0x12345678
    assert decoded.stable_id == "pid:12345678:ot:0000:0000"
    assert decoded.address == 0x02290004


def test_pc_storage_payload_skips_empty_and_checksum_invalid_box_slots() -> None:
    valid_record = _boxed_fixture()
    invalid_record = bytearray(_boxed_fixture(pid=0x87654321, species_id=403, nickname="stripe"))
    invalid_record[0x20] ^= 0x40
    payload = {
        "scan_ok": True,
        "save_data_pointer": "0x022711C8",
        "save_data_body_address": "0x022711C8",
        "save_data_struct_address_inferred": "0x022711B4",
        "page_info_address": "0x02291428",
        "page_id": PC_BOXES_PAGE_ID,
        "page_block_id": 1,
        "page_location": "0x00008000",
        "page_size": "0x121D0",
        "pc_boxes_address": "0x022791C8",
        "records_address": "0x022791CC",
        "records_offset": "0x002791CC",
        "final_address_valid": True,
        "box_count": 18,
        "slots_per_box": 30,
        "record_stride": 136,
        "records_scanned": 540,
        "bytes_read": PC_BOX_RECORDS_SIZE,
        "frame": 120,
        "scan_duration_ms": 11,
        "records": [
            {"box": 1, "slot": 1, "address": "0x022791CC", "raw_hex": valid_record.hex()},
            {"box": 2, "slot": 30, "address": "0x0227B144", "raw_hex": bytes(136).hex()},
            {"box": 3, "slot": 4, "address": "0x0227B344", "raw_hex": invalid_record.hex()},
        ],
    }

    storage = decode_pc_storage_payload(payload)

    assert storage is not None and storage.available
    assert storage.box_count == 18
    assert storage.slots_per_box == 30
    assert storage.record_stride == 136
    assert storage.records_scanned == 540
    assert storage.bytes_read == 73_440
    assert len(storage.records) == 2
    assert len(storage.valid_pokemon) == 1
    assert storage.records[0].box_index == 1
    assert storage.records[1].box_index == 3
    assert storage.records[0].raw_hex.replace(" ", "").lower() == valid_record.hex()
    assert storage.checksum_failures == 1
    assert storage.empty_records == 538
    assert storage.page_id == 37
    assert storage.page_block_id == 1
    assert PC_BOXES_PAGE_SIZE == 0x121D0


def test_pc_storage_payload_rejects_inconsistent_runtime_layout_metadata() -> None:
    state = decode_pc_storage_payload(
        {
            "scan_ok": True,
            "page_id": 47,
            "page_block_id": 1,
            "page_size": hex(PC_BOXES_PAGE_SIZE),
            "page_location": "0x8000",
            "box_count": 18,
            "slots_per_box": 30,
            "record_stride": 136,
            "records_scanned": 540,
            "records": [],
        }
    )

    assert state is not None
    assert not state.available
    assert "metadata" in state.error


def test_pc_storage_accepts_decomp_save_table_index_for_pc_boxes() -> None:
    payload = _pc_storage_scan_payload(60, ())

    state = decode_pc_storage_payload(payload)

    assert state is not None
    assert state.available
    assert state.page_id == 37
    assert state.save_data_body_address == 0x022711C8
    assert state.save_data_struct_address_inferred == 0x022711B4
    assert state.page_info_address == 0x02291428
    assert state.pc_boxes_address == 0x022791C8
    assert state.records_address == 0x022791CC
    assert state.bytes_read == 73_440
    assert state.empty_records == 540


def test_pc_storage_skips_zero_header_empty_slot_with_nonzero_payload() -> None:
    empty = bytes(8) + bytes([0xA5]) * (136 - 8)
    payload = _pc_storage_scan_payload(60, ((1, 1, empty),))

    state = decode_pc_storage_payload(payload)

    assert state is not None and state.available
    assert state.records == ()
    assert state.empty_records == 540


def test_pc_storage_accepts_only_current_lua_session_cache_metadata() -> None:
    address = 0x02080000
    payload = _pc_storage_scan_payload(60, ())
    payload.update(
        {
            "pc_storage_resolver_kind": "session_pc_cache",
            "pc_storage_resolver_status": "resolved for this session",
            "session_pc_first_record_address": f"0x{address:08X}",
            "lua_run_id": "lua-current",
            "session_pc_run_id": "lua-current",
            "pc_boxes_address": f"0x{address:08X}",
            "records_address": f"0x{address:08X}",
            "records_offset": hex(address - 0x02000000),
            "final_address_valid": True,
        }
    )

    current = decode_pc_storage_payload(payload)
    assert current is not None and current.available
    assert current.session_pc_first_record_address == address
    assert current.lua_run_id == "lua-current"

    payload["session_pc_run_id"] = "previous-lua"
    stale = decode_pc_storage_payload(payload)
    assert stale is not None and not stale.available
    assert "Session PC resolver metadata is invalid" in stale.error


def test_session_pc_cache_rejects_malformed_occupied_record() -> None:
    address = 0x02080000
    record = bytearray(_boxed_fixture())
    record[0x20] ^= 0x40
    payload = _pc_storage_scan_payload(60, ((1, 1, bytes(record)),))
    payload.update({
        "pc_storage_resolver_kind": "session_pc_cache",
        "pc_storage_resolver_status": "resolved for this session",
        "session_pc_first_record_address": f"0x{address:08X}",
        "lua_run_id": "lua-current",
        "session_pc_run_id": "lua-current",
        "pc_boxes_address": f"0x{address:08X}",
        "records_address": f"0x{address:08X}",
        "records_offset": hex(address - 0x02000000),
    })
    payload["records"][0]["address"] = f"0x{address:08X}"

    state = decode_pc_storage_payload(payload)

    assert state is not None and not state.available
    assert "malformed occupied record" in state.error

    payload["malformed_records"] = 1
    state = decode_pc_storage_payload(payload)
    assert state is not None and not state.available
    assert "metadata" in state.error


def test_pc_storage_runtime_page_validation_failure_is_reported_not_decoded() -> None:
    state = decode_pc_storage_payload(
        {"scan_ok": False, "scan_error": "SaveData PC page validation failed."}
    )

    assert state is not None
    assert not state.available
    assert state.error == "SaveData PC page validation failed."
    assert state.records == ()


def test_pc_storage_null_pointer_failure_is_reported_not_decoded() -> None:
    state = decode_pc_storage_payload(
        {
            "scan_ok": False,
            "scan_error": "Runtime base pointer is unavailable or outside Main RAM.",
            "save_data_pointer": None,
            "records": [],
        }
    )

    assert state is not None
    assert not state.available
    assert state.save_data_pointer is None
    assert "Runtime base pointer" in state.error
    assert state.records == ()


def test_pc_storage_out_of_range_pointer_failure_is_reported_not_decoded() -> None:
    state = decode_pc_storage_payload(
        {
            "scan_ok": False,
            "scan_error": "Runtime base pointer is outside Main RAM.",
            "save_data_pointer": "0x03000000",
            "save_data_body_address": "0x03000000",
            "records": [],
        }
    )

    assert state is not None
    assert not state.available
    assert state.save_data_body_address == 0x03000000
    assert "outside Main RAM" in state.error
    assert state.records == ()


def test_pc_storage_rejects_old_full_savedata_struct_pageinfo_math() -> None:
    payload = _pc_storage_scan_payload(60, ())
    payload["page_info_address"] = "0x0229143C"
    payload["pc_boxes_address"] = "0x022791DC"
    payload["records_address"] = "0x022791E0"
    payload["records_offset"] = "0x002791E0"

    state = decode_pc_storage_payload(payload)

    assert state is not None
    assert not state.available
    assert "metadata" in state.error


def test_pc_storage_rejects_impossible_zero_page_location() -> None:
    payload = _pc_storage_scan_payload(60, ())
    payload["page_location"] = "0x0"
    payload["pc_boxes_address"] = payload["save_data_body_address"]
    payload["records_address"] = hex(int(payload["pc_boxes_address"], 16) + 4)
    payload["records_offset"] = hex(int(payload["records_address"], 16) - 0x02000000)

    state = decode_pc_storage_payload(payload)

    assert state is not None
    assert not state.available
    assert "metadata" in state.error


def test_new_box_identity_must_have_two_stable_checksums_before_acquisition() -> None:
    source = BizHawkRamDataSource(server=object())
    empty_payload = _pc_storage_scan_payload(60, ())
    empty_state = decode_pc_storage_payload(empty_payload)
    assert empty_state is not None
    source._stabilize_pc_storage_payload(empty_state, SimpleNamespace(payload=empty_payload))

    record = _boxed_fixture(pid=0xAABBCCDD, species_id=63, nickname="abra")
    first_payload = _pc_storage_scan_payload(120, ((1, 1, record),))
    first_state = decode_pc_storage_payload(first_payload)
    assert first_state is not None
    first_stable = source._stabilize_pc_storage_payload(
        first_state, SimpleNamespace(payload=first_payload)
    )
    assert first_stable == ()

    second_payload = _pc_storage_scan_payload(180, ((1, 1, record),))
    second_state = decode_pc_storage_payload(second_payload)
    assert second_state is not None
    second_stable = source._stabilize_pc_storage_payload(
        second_state, SimpleNamespace(payload=second_payload)
    )
    assert [mon.species_id for mon in second_stable] == [63]
    assert [mon.species_id for mon in source._pc_storage_changes] == [63]


def test_session_pc_monitor_does_not_rebaseline_when_party_pointer_moves() -> None:
    source = BizHawkRamDataSource(server=object())
    existing = _boxed_fixture(pid=0x10203040, species_id=415, nickname="mitsu")
    caught = _boxed_fixture(pid=0x11223344, species_id=74, nickname="rocky")
    first_address = 0x02080000

    def scan(frame, records, party_pointer):
        payload = _pc_storage_scan_payload(frame, records)
        payload.update({
            "pc_storage_resolver_kind": "session_pc_cache",
            "pc_storage_resolver_status": "resolved for this session",
            "session_pc_first_record_address": f"0x{first_address:08X}",
            "lua_run_id": "same-lua-run",
            "session_pc_run_id": "same-lua-run",
            "rom_session_identity": "same-rom",
            "save_data_pointer": hex(party_pointer),
            "pc_boxes_address": f"0x{first_address:08X}",
            "records_address": f"0x{first_address:08X}",
            "records_offset": hex(first_address - 0x02000000),
        })
        for item in payload["records"]:
            item["address"] = hex(
                first_address + ((item["box"] - 1) * 30 + item["slot"] - 1) * 136
            )
        state = decode_pc_storage_payload(payload)
        assert state is not None and state.available
        return state, SimpleNamespace(payload=payload)

    baseline = scan(60, ((1, 1, existing),), 0x02271140)
    source._stabilize_pc_storage_payload(*baseline)
    assert source._pc_storage_monitor_debug["status"] == "baseline"

    first_sighting = scan(120, ((1, 1, existing), (1, 2, caught)), 0x02272140)
    assert [mon.stable_id for mon in source._stabilize_pc_storage_payload(*first_sighting)] == [
        "pid:10203040:ot:0000:0000"
    ]
    assert len(source._pc_storage_monitor_debug["added"]) == 1
    assert len(source._pc_storage_monitor_debug["pending_stability"]) == 1

    stable_sighting = scan(420, ((1, 1, existing), (1, 2, caught)), 0x02272140)
    source._stabilize_pc_storage_payload(*stable_sighting)
    assert [mon.stable_id for mon in source._pc_storage_changes] == [
        "pid:11223344:ot:0000:0000"
    ]
    assert source._pc_storage_monitor_debug["stable_new"][0]["slot"] == 2


def test_first_valid_pc_scan_baselines_existing_boxed_pokemon_immediately() -> None:
    source = BizHawkRamDataSource(server=object())
    existing = _boxed_fixture(pid=0x10203040, species_id=399, nickname="bidoof")
    payload = _pc_storage_scan_payload(60, ((4, 12, existing),))
    state = decode_pc_storage_payload(payload)
    assert state is not None

    stable = source._stabilize_pc_storage_payload(state, SimpleNamespace(payload=payload))

    assert len(stable) == 1
    assert stable[0].stable_id == "pid:10203040:ot:0000:0000"


def test_pc_storage_payload_rejects_misaddressed_box_records() -> None:
    payload = _pc_storage_scan_payload(
        60, ((1, 1, _boxed_fixture(pid=0xAABBCCDD, species_id=63, nickname="abra")),)
    )
    payload["records"][0]["address"] = "0x02280000"

    state = decode_pc_storage_payload(payload)

    assert state is not None
    assert not state.available
    assert "record addresses" in state.error


def _plain_box(
    species_id: int,
    evs: tuple[int, int, int, int, int, int],
    nickname_codes: tuple[int, ...] = (),
    nickname_terminated: bool = True,
    held_item_id: int = 0,
    ability_id: int = 0,
    ivs: tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0),
    is_egg: bool = False,
    has_nickname: bool = False,
    friendship: int = 0,
    moves: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> bytes:
    hp, attack, defense, speed, special_attack, special_defense = evs
    data = bytearray(BOX_DATA_SIZE)
    data[0x00:0x02] = species_id.to_bytes(2, "little")
    data[0x02:0x04] = held_item_id.to_bytes(2, "little")
    data[FRIENDSHIP_BOX_DATA_OFFSET] = friendship
    data[0x0D] = ability_id
    data[0x08:0x0C] = (125000).to_bytes(4, "little")
    data[0x10:0x16] = bytes((hp, attack, defense, speed, special_attack, special_defense))
    for index, move_id in enumerate(moves):
        offset = 0x20 + index * 2
        data[offset : offset + 2] = move_id.to_bytes(2, "little")
    hp_iv, attack_iv, defense_iv, speed_iv, special_attack_iv, special_defense_iv = ivs
    packed_ivs = (
        hp_iv
        | (attack_iv << 5)
        | (defense_iv << 10)
        | (speed_iv << 15)
        | (special_attack_iv << 20)
        | (special_defense_iv << 25)
        | (int(is_egg) << 30)
        | (int(has_nickname) << 31)
    )
    data[0x30:0x34] = packed_ivs.to_bytes(4, "little")
    if nickname_codes:
        encoded = b"".join(code.to_bytes(2, "little") for code in nickname_codes)
        if nickname_terminated:
            encoded += b"\xff\xff"
        data[0x40 : 0x40 + len(encoded)] = encoded
    return bytes(data)


def _party_record(
    pid: int,
    species_id: int,
    evs: tuple[int, int, int, int, int, int],
    level: int = 10,
    current_hp: int = 30,
    max_hp: int = 60,
    nickname: str | None = None,
    nickname_codes: tuple[int, ...] = (),
    nickname_terminated: bool = True,
    held_item_id: int = 0,
    ability_id: int = 0,
    ivs: tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0),
    is_egg: bool = False,
    has_nickname: bool = False,
    attack_stat: int = 49,
    defense_stat: int = 49,
    speed_stat: int = 45,
    special_attack_stat: int = 65,
    special_defense_stat: int = 65,
    friendship: int = 0,
    moves: tuple[int, int, int, int] = (0, 0, 0, 0),
    move_pps: tuple[int, int, int, int] = (0, 0, 0, 0),
    move_pp_ups: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> bytes:
    if nickname is not None:
        nickname_codes = _encode_gen4_nickname(nickname)
    plain = bytearray(_plain_box(
        species_id,
        evs,
        nickname_codes,
        nickname_terminated,
        held_item_id,
        ability_id,
        ivs,
        is_egg,
        has_nickname,
        friendship,
        moves,
    ))
    plain[0x28:0x2C] = bytes(move_pps)
    plain[0x2C:0x30] = bytes(move_pp_ups)
    plain = bytes(plain)
    checksum = calculate_checksum(plain)
    encrypted = encrypt_box_data(plain, pid, checksum)
    record = bytearray(PARTY_POKEMON_SIZE)
    record[0x00:0x04] = pid.to_bytes(4, "little")
    record[0x06:0x08] = checksum.to_bytes(2, "little")
    record[0x08 : 0x08 + BOX_DATA_SIZE] = encrypted
    battle_stats = bytearray(0x14)
    battle_stats[0x04] = level
    battle_stats[0x06:0x08] = current_hp.to_bytes(2, "little")
    battle_stats[0x08:0x0A] = max_hp.to_bytes(2, "little")
    battle_stats[0x0A:0x0C] = attack_stat.to_bytes(2, "little")
    battle_stats[0x0C:0x0E] = defense_stat.to_bytes(2, "little")
    battle_stats[0x0E:0x10] = speed_stat.to_bytes(2, "little")
    battle_stats[0x10:0x12] = special_attack_stat.to_bytes(2, "little")
    battle_stats[0x12:0x14] = special_defense_stat.to_bytes(2, "little")
    record[0x88:0x9C] = xor_words(bytes(battle_stats), pid)
    return bytes(record)


def _encode_gen4_nickname(nickname: str) -> tuple[int, ...]:
    codes = []
    for character in nickname:
        if "0" <= character <= "9":
            codes.append(0x121 + ord(character) - ord("0"))
        elif "A" <= character <= "Z":
            codes.append(0x12B + ord(character) - ord("A"))
        elif "a" <= character <= "z":
            codes.append(0x145 + ord(character) - ord("a"))
        elif character == " ":
            codes.append(0x1DE)
        elif character == "é":
            codes.append(0x188)
        elif character == "!":
            codes.append(0x1AB)
        else:
            raise ValueError(f"Test helper does not encode {character!r}.")
    return tuple(codes)
