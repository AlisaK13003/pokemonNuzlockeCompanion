"""Decode party and boxed Generation IV Pokemon records."""

from __future__ import annotations

from dataclasses import dataclass

from pokemon_ev_tracker.pokemon.gen4.crypto import (
    BOX_DATA_SIZE,
    block_order,
    calculate_checksum,
    decrypt_box_data,
    shuffle_index,
    xor_words,
)
from pokemon_ev_tracker.pokemon.gen4.ivs import IndividualValues, decode_individual_values
from pokemon_ev_tracker.pokemon.gen4.nature import nature_from_pid

PARTY_POKEMON_SIZE = 236
BOX_POKEMON_SIZE = 0x88
POKEMON_HEADER_SIZE = 0x08
HELD_ITEM_BOX_DATA_OFFSET = 0x02
HELD_ITEM_RECORD_OFFSET = POKEMON_HEADER_SIZE + HELD_ITEM_BOX_DATA_OFFSET
ABILITY_BOX_DATA_OFFSET = 0x0D
IVS_RECORD_OFFSET = 0x38
# Published PKM offsets include this header; decrypt_box_data returns bytes after it.
IVS_BOX_DATA_OFFSET = IVS_RECORD_OFFSET - POKEMON_HEADER_SIZE
NICKNAME_RECORD_OFFSET = 0x48
NICKNAME_BOX_DATA_OFFSET = NICKNAME_RECORD_OFFSET - POKEMON_HEADER_SIZE
NICKNAME_FIELD_SIZE = 0x16
# PKHeX's PK4 fields include the 8-byte header; decrypted payload offsets subtract it.
MET_LOCATION_EXTENDED_RECORD_OFFSET = 0x46
MET_LOCATION_EXTENDED_BOX_DATA_OFFSET = MET_LOCATION_EXTENDED_RECORD_OFFSET - POKEMON_HEADER_SIZE
EGG_LOCATION_EXTENDED_RECORD_OFFSET = 0x44
EGG_LOCATION_EXTENDED_BOX_DATA_OFFSET = EGG_LOCATION_EXTENDED_RECORD_OFFSET - POKEMON_HEADER_SIZE
ORIGIN_GAME_RECORD_OFFSET = 0x5F
ORIGIN_GAME_BOX_DATA_OFFSET = ORIGIN_GAME_RECORD_OFFSET - POKEMON_HEADER_SIZE
MET_DATE_RECORD_OFFSET = 0x7B
MET_DATE_BOX_DATA_OFFSET = MET_DATE_RECORD_OFFSET - POKEMON_HEADER_SIZE
MET_LOCATION_DP_RECORD_OFFSET = 0x80
MET_LOCATION_DP_BOX_DATA_OFFSET = MET_LOCATION_DP_RECORD_OFFSET - POKEMON_HEADER_SIZE
MET_LEVEL_RECORD_OFFSET = 0x84
MET_LEVEL_BOX_DATA_OFFSET = MET_LEVEL_RECORD_OFFSET - POKEMON_HEADER_SIZE
FRIENDSHIP_RECORD_OFFSET = 0x14
FRIENDSHIP_BOX_DATA_OFFSET = FRIENDSHIP_RECORD_OFFSET - POKEMON_HEADER_SIZE
MOVES_BOX_DATA_OFFSET = 0x20
MOVE_SLOT_COUNT = 4
MOVE_PP_BOX_DATA_OFFSET = MOVES_BOX_DATA_OFFSET + MOVE_SLOT_COUNT * 2
MOVE_PP_UPS_BOX_DATA_OFFSET = MOVE_PP_BOX_DATA_OFFSET + MOVE_SLOT_COUNT

_GEN4_INTL_CHARACTERS = (
    "\0　ぁあぃいぅうぇえぉおかがきぎ"
    "くぐけげこごさざしじすずせぜそぞ"
    "ただちぢっつづてでとどなにぬねの"
    "はばぱひびぴふぶぷへべぺほぼぽま"
    "みむめもゃやゅゆょよらりるれろわ"
    "をんァアィイゥウェエォオカガキギ"
    "クグケゲコゴサザシジスズセゼソゾ"
    "タダチヂッツヅテデトドナニヌネノ"
    "ハバパヒビピフブプヘベペホボポマ"
    "ミムメモャヤュユョヨラリルレロワ"
    "ヲン０１２３４５６７８９ＡＢＣＤ"
    "ＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴ"
    "ＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊ"
    "ｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    "\uffff！？，。…・／「」『』（）♂♀"
    "＋ー×÷＝～：；．，♠♣♥♦★◎"
    "○□△◇＠♪％☀☁☂☃①②③④⑤"
    "⑥⑦円♈♉♊♋♌♍♎♏←↑↓→►"
    "＆0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ⑧ØÙÚÛÜÝÞß"
    "àáâãäåæçèéêëìíîïðñòóôõö⑨øùúûüýþÿ"
    "ŒœŞşªº⑩⑪⑫$¡¿!?,.⑬･/‘'“”„«»()♂♀+-*"
    "#=&~:;⑯⑰⑱⑲⑳⑴⑵⑶⑷⑸@⑹%⑺⑻⑼⑽⑾⑿⒀⒁⒂⒃⒄"
    " ⒅⒆⒇⒈⒉⒊⒋⒌⒍°_＿⒎⒏"
)
_GEN4_INTL_CHAR_MAP = {value: character for value, character in enumerate(_GEN4_INTL_CHARACTERS)}


@dataclass(frozen=True)
class EVs:
    hp: int
    attack: int
    defense: int
    special_attack: int
    special_defense: int
    speed: int

    @property
    def total(self) -> int:
        return (
            self.hp
            + self.attack
            + self.defense
            + self.special_attack
            + self.special_defense
            + self.speed
        )


@dataclass(frozen=True)
class PokemonDiagnostics:
    address: int | None
    pid: int
    checksum: int
    calculated_checksum: int
    checksum_valid: bool
    shuffle_index: int
    block_order: str
    sanity_bytes: str
    battle_stats_raw_hex: str
    battle_stats_decrypted_hex: str
    battle_stats_valid: bool
    battle_stats_error: str | None


@dataclass(frozen=True)
class CurrentStats:
    max_hp: int
    attack: int
    defense: int
    speed: int
    special_attack: int
    special_defense: int


@dataclass(frozen=True)
class NicknameDiagnostics:
    absolute_address: int | None
    record_relative_offset: int
    box_data_relative_offset: int
    raw_bytes_hex: str
    code_units: tuple[int, ...]
    decoded_characters: tuple[str | None, ...]
    terminator_unit_index: int | None
    decoded_string: str | None


@dataclass(frozen=True)
class AcquisitionMetadataDiagnostics:
    met_location_record_offset: int
    met_location_box_data_offset: int
    egg_location_record_offset: int
    egg_location_box_data_offset: int
    origin_game_record_offset: int
    origin_game_box_data_offset: int
    met_date_record_offset: int
    met_date_box_data_offset: int
    met_location_dp_record_offset: int
    met_location_dp_box_data_offset: int
    met_level_record_offset: int
    met_level_box_data_offset: int


@dataclass(frozen=True)
class DecodedPokemon:
    species_id: int
    held_item_id: int
    friendship: int
    move_ids: tuple[int, int, int, int]
    move_current_pps: tuple[int, int, int, int]
    move_pp_ups: tuple[int, int, int, int]
    experience: int
    evs: EVs
    level: int | None
    current_hp: int | None
    max_hp: int | None
    nature_id: int
    nature_name: str
    nature_increased_stat: str | None
    nature_decreased_stat: str | None
    ability_id: int
    packed_ivs: int
    ivs: IndividualValues
    current_stats: CurrentStats
    nickname: str | None
    nickname_diagnostics: NicknameDiagnostics
    met_location_id: int
    met_location_dp_id: int
    egg_location_id: int
    origin_game: int
    met_level: int | None
    met_date: tuple[int, int, int] | None
    is_egg: bool
    stable_id: str
    acquisition_metadata_diagnostics: AcquisitionMetadataDiagnostics
    diagnostics: PokemonDiagnostics


@dataclass(frozen=True)
class DecodedBoxPokemon:
    species_id: int
    nickname: str | None
    nickname_diagnostics: NicknameDiagnostics
    met_location_id: int
    met_location_dp_id: int
    egg_location_id: int
    origin_game: int
    met_level: int | None
    is_egg: bool
    stable_id: str
    pid: int
    checksum: int
    calculated_checksum: int
    checksum_valid: bool
    address: int | None
    shuffle_index: int
    block_order: str


def decode_party_pokemon(
    data: bytes,
    address: int | None = None,
) -> DecodedPokemon:
    if len(data) < PARTY_POKEMON_SIZE:
        raise ValueError("Party Pokemon record must be 236 bytes.")

    pid = int.from_bytes(data[0x00:0x04], "little")
    checksum = int.from_bytes(data[0x06:0x08], "little")
    decrypted = decrypt_box_data(data[0x08 : 0x08 + BOX_DATA_SIZE], pid, checksum)
    calculated_checksum = calculate_checksum(decrypted)
    checksum_valid = calculated_checksum == checksum
    battle_stats_raw = data[0x88:0x9C]
    battle_stats = xor_words(battle_stats_raw, pid)

    species_id = int.from_bytes(decrypted[0x00:0x02], "little")
    held_item_id = int.from_bytes(
        decrypted[HELD_ITEM_BOX_DATA_OFFSET : HELD_ITEM_BOX_DATA_OFFSET + 2], "little"
    )
    friendship = decrypted[FRIENDSHIP_BOX_DATA_OFFSET]
    move_ids = tuple(
        int.from_bytes(decrypted[offset : offset + 2], "little")
        for offset in range(MOVES_BOX_DATA_OFFSET, MOVES_BOX_DATA_OFFSET + MOVE_SLOT_COUNT * 2, 2)
    )
    move_current_pps = tuple(
        decrypted[MOVE_PP_BOX_DATA_OFFSET : MOVE_PP_BOX_DATA_OFFSET + MOVE_SLOT_COUNT]
    )
    move_pp_ups = tuple(
        decrypted[MOVE_PP_UPS_BOX_DATA_OFFSET : MOVE_PP_UPS_BOX_DATA_OFFSET + MOVE_SLOT_COUNT]
    )
    experience = int.from_bytes(decrypted[0x08:0x0C], "little")
    nature = nature_from_pid(pid)
    ability_id = decrypted[ABILITY_BOX_DATA_OFFSET]
    packed_ivs = int.from_bytes(
        decrypted[IVS_BOX_DATA_OFFSET : IVS_BOX_DATA_OFFSET + 4], "little"
    )
    ivs = decode_individual_values(packed_ivs)
    evs = EVs(
        hp=decrypted[0x10],
        attack=decrypted[0x11],
        defense=decrypted[0x12],
        speed=decrypted[0x13],
        special_attack=decrypted[0x14],
        special_defense=decrypted[0x15],
    )
    nickname_raw = decrypted[
        NICKNAME_BOX_DATA_OFFSET : NICKNAME_BOX_DATA_OFFSET + NICKNAME_FIELD_SIZE
    ]
    nickname_diagnostics = _decode_nickname(nickname_raw, address)
    met_location_id = int.from_bytes(
        decrypted[
            MET_LOCATION_EXTENDED_BOX_DATA_OFFSET : MET_LOCATION_EXTENDED_BOX_DATA_OFFSET + 2
        ],
        "little",
    )
    met_location_dp_id = int.from_bytes(
        decrypted[MET_LOCATION_DP_BOX_DATA_OFFSET : MET_LOCATION_DP_BOX_DATA_OFFSET + 2],
        "little",
    )
    egg_location_id = int.from_bytes(
        decrypted[
            EGG_LOCATION_EXTENDED_BOX_DATA_OFFSET : EGG_LOCATION_EXTENDED_BOX_DATA_OFFSET + 2
        ],
        "little",
    )
    origin_game = decrypted[ORIGIN_GAME_BOX_DATA_OFFSET]
    met_level_value = decrypted[MET_LEVEL_BOX_DATA_OFFSET] & 0x7F
    met_level = met_level_value or None
    raw_met_date = decrypted[MET_DATE_BOX_DATA_OFFSET : MET_DATE_BOX_DATA_OFFSET + 3]
    met_date = tuple(raw_met_date) if len(raw_met_date) == 3 and any(raw_met_date) else None
    trainer_id = int.from_bytes(decrypted[0x04:0x06], "little")
    secret_id = int.from_bytes(decrypted[0x06:0x08], "little")
    level = battle_stats[0x04]
    current_hp = int.from_bytes(battle_stats[0x06:0x08], "little")
    max_hp = int.from_bytes(battle_stats[0x08:0x0A], "little")
    current_stats = CurrentStats(
        max_hp=max_hp,
        attack=int.from_bytes(battle_stats[0x0A:0x0C], "little"),
        defense=int.from_bytes(battle_stats[0x0C:0x0E], "little"),
        speed=int.from_bytes(battle_stats[0x0E:0x10], "little"),
        special_attack=int.from_bytes(battle_stats[0x10:0x12], "little"),
        special_defense=int.from_bytes(battle_stats[0x12:0x14], "little"),
    )
    battle_stats_error = _validate_battle_stats(level, current_hp, max_hp, current_stats)

    return DecodedPokemon(
        species_id=species_id,
        held_item_id=held_item_id,
        friendship=friendship,
        move_ids=move_ids,
        move_current_pps=move_current_pps,
        move_pp_ups=move_pp_ups,
        experience=experience,
        evs=evs,
        level=level,
        current_hp=current_hp,
        max_hp=max_hp,
        nature_id=nature.id,
        nature_name=nature.name,
        nature_increased_stat=nature.increased_stat,
        nature_decreased_stat=nature.decreased_stat,
        ability_id=ability_id,
        packed_ivs=packed_ivs,
        ivs=ivs,
        current_stats=current_stats,
        nickname=nickname_diagnostics.decoded_string,
        nickname_diagnostics=nickname_diagnostics,
        met_location_id=met_location_id,
        met_location_dp_id=met_location_dp_id,
        egg_location_id=egg_location_id,
        origin_game=origin_game,
        met_level=met_level,
        met_date=met_date,
        is_egg=ivs.is_egg,
        stable_id=f"pid:{pid:08X}:ot:{trainer_id:04X}:{secret_id:04X}",
        acquisition_metadata_diagnostics=AcquisitionMetadataDiagnostics(
            met_location_record_offset=MET_LOCATION_EXTENDED_RECORD_OFFSET,
            met_location_box_data_offset=MET_LOCATION_EXTENDED_BOX_DATA_OFFSET,
            egg_location_record_offset=EGG_LOCATION_EXTENDED_RECORD_OFFSET,
            egg_location_box_data_offset=EGG_LOCATION_EXTENDED_BOX_DATA_OFFSET,
            origin_game_record_offset=ORIGIN_GAME_RECORD_OFFSET,
            origin_game_box_data_offset=ORIGIN_GAME_BOX_DATA_OFFSET,
            met_date_record_offset=MET_DATE_RECORD_OFFSET,
            met_date_box_data_offset=MET_DATE_BOX_DATA_OFFSET,
            met_location_dp_record_offset=MET_LOCATION_DP_RECORD_OFFSET,
            met_location_dp_box_data_offset=MET_LOCATION_DP_BOX_DATA_OFFSET,
            met_level_record_offset=MET_LEVEL_RECORD_OFFSET,
            met_level_box_data_offset=MET_LEVEL_BOX_DATA_OFFSET,
        ),
        diagnostics=PokemonDiagnostics(
            address=address,
            pid=pid,
            checksum=checksum,
            calculated_checksum=calculated_checksum,
            checksum_valid=checksum_valid,
            shuffle_index=shuffle_index(pid),
            block_order=block_order(pid),
            sanity_bytes=data[:16].hex(" ").upper(),
            battle_stats_raw_hex=battle_stats_raw.hex(" ").upper(),
            battle_stats_decrypted_hex=battle_stats.hex(" ").upper(),
            battle_stats_valid=battle_stats_error is None,
            battle_stats_error=battle_stats_error,
        ),
    )


def decode_box_pokemon(data: bytes, address: int | None = None) -> DecodedBoxPokemon:
    """Decode the shared encrypted PK4 box portion without party-only stats."""
    if len(data) < BOX_POKEMON_SIZE:
        raise ValueError("Box Pokemon record must be 136 bytes.")

    pid = int.from_bytes(data[0x00:0x04], "little")
    checksum = int.from_bytes(data[0x06:0x08], "little")
    decrypted = decrypt_box_data(data[0x08:BOX_POKEMON_SIZE], pid, checksum)
    calculated_checksum = calculate_checksum(decrypted)
    ivs = decode_individual_values(
        int.from_bytes(decrypted[IVS_BOX_DATA_OFFSET : IVS_BOX_DATA_OFFSET + 4], "little")
    )
    nickname_raw = decrypted[
        NICKNAME_BOX_DATA_OFFSET : NICKNAME_BOX_DATA_OFFSET + NICKNAME_FIELD_SIZE
    ]
    nickname = _decode_nickname(nickname_raw, address)
    trainer_id = int.from_bytes(decrypted[0x04:0x06], "little")
    secret_id = int.from_bytes(decrypted[0x06:0x08], "little")
    met_level_value = decrypted[MET_LEVEL_BOX_DATA_OFFSET] & 0x7F

    return DecodedBoxPokemon(
        species_id=int.from_bytes(decrypted[0x00:0x02], "little"),
        nickname=nickname.decoded_string,
        nickname_diagnostics=nickname,
        met_location_id=int.from_bytes(
            decrypted[
                MET_LOCATION_EXTENDED_BOX_DATA_OFFSET : MET_LOCATION_EXTENDED_BOX_DATA_OFFSET + 2
            ],
            "little",
        ),
        met_location_dp_id=int.from_bytes(
            decrypted[MET_LOCATION_DP_BOX_DATA_OFFSET : MET_LOCATION_DP_BOX_DATA_OFFSET + 2],
            "little",
        ),
        egg_location_id=int.from_bytes(
            decrypted[
                EGG_LOCATION_EXTENDED_BOX_DATA_OFFSET : EGG_LOCATION_EXTENDED_BOX_DATA_OFFSET + 2
            ],
            "little",
        ),
        origin_game=decrypted[ORIGIN_GAME_BOX_DATA_OFFSET],
        met_level=met_level_value or None,
        is_egg=ivs.is_egg,
        stable_id=f"pid:{pid:08X}:ot:{trainer_id:04X}:{secret_id:04X}",
        pid=pid,
        checksum=checksum,
        calculated_checksum=calculated_checksum,
        checksum_valid=calculated_checksum == checksum,
        address=address,
        shuffle_index=shuffle_index(pid),
        block_order=block_order(pid),
    )


def _validate_battle_stats(
    level: int, current_hp: int, max_hp: int, stats: CurrentStats
) -> str | None:
    if not 1 <= level <= 100:
        return f"Level {level} is outside the supported range 1-100."
    if not 1 <= max_hp <= 714:
        return f"Max HP {max_hp} is outside the possible range 1-714."
    if current_hp > max_hp:
        return f"Current HP {current_hp} exceeds max HP {max_hp}."
    for name, value in (
        ("Attack", stats.attack),
        ("Defense", stats.defense),
        ("Speed", stats.speed),
        ("Special Attack", stats.special_attack),
        ("Special Defense", stats.special_defense),
    ):
        if not 1 <= value <= 999:
            return f"{name} {value} is outside the possible range 1-999."
    return None


def _decode_nickname(raw: bytes, record_address: int | None) -> NicknameDiagnostics:
    code_units = tuple(
        int.from_bytes(raw[index : index + 2], "little") for index in range(0, len(raw) - 1, 2)
    )
    decoded_characters: list[str | None] = []
    decoded_text: list[str] = []
    terminator_unit_index = None
    invalid = len(raw) % 2 != 0

    for index, code in enumerate(code_units):
        if terminator_unit_index is not None:
            decoded_characters.append(None)
            continue
        if code in (0xFFFF, 0x0000):
            terminator_unit_index = index
            decoded_characters.append(None)
            continue
        character = _GEN4_INTL_CHAR_MAP.get(code)
        if character is None:
            invalid = True
            decoded_characters.append(None)
            continue
        if character == "\uffff":
            decoded_characters.append(None)
            continue
        decoded_characters.append(character)
        decoded_text.append(character)

    decoded_string = "".join(decoded_text) if decoded_text and not invalid else None
    return NicknameDiagnostics(
        absolute_address=(
            record_address + NICKNAME_RECORD_OFFSET if record_address is not None else None
        ),
        record_relative_offset=NICKNAME_RECORD_OFFSET,
        box_data_relative_offset=NICKNAME_BOX_DATA_OFFSET,
        raw_bytes_hex=raw.hex(" ").upper(),
        code_units=code_units,
        decoded_characters=tuple(decoded_characters),
        terminator_unit_index=terminator_unit_index,
        decoded_string=decoded_string,
    )
