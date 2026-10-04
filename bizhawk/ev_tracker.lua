-- BizHawk RAM-1 probe for the Pokemon Platinum EV Tracker.
-- Run this from EmuHawk's Lua Console while the Nintendo DS core is active.

local HOST = "127.0.0.1"
local PORT = 46387
local LUA_BUILD_ID = "pc-storage-v24-cache-validation"
local LUA_GAME_ID = "pokemon-platinum"
local LUA_GAME_VERSION = "gen4-platinum-us"
local HEARTBEAT_INTERVAL = 120
local PARTY_INTERVAL = 30
local PC_STORAGE_INTERVAL = 60
local DOMAIN_REFRESH_INTERVAL = 600
local CONNECT_RETRY_INTERVAL = 300
local CONNECT_TIMEOUT_SECONDS = 0.03
local COMMAND_FILE_POLL_INTERVAL = 15
local WALK_REVERSAL_GRACE_FRAMES = 8
local MAX_FALLBACK_BYTES = 16 * 1024 * 1024
local COORDINATE_SCAN_CHUNK_BYTES = 16384
local MAX_COORDINATE_SCAN_BYTES = 0x400000
local TEMP_ROOT = os.getenv("TEMP") or os.getenv("TMP") or os.getenv("TMPDIR")
local FALLBACK_FILE = TEMP_ROOT and (TEMP_ROOT .. "\\ev_tracker_bizhawk.jsonl") or nil
local COMMAND_FILE = TEMP_ROOT and (TEMP_ROOT .. "\\ev_tracker_bizhawk_command.txt") or nil
local COORDINATE_SCAN_FILE = TEMP_ROOT and (TEMP_ROOT .. "\\ev_tracker_bizhawk_coordinates.jsonl") or nil
local MAIN_RAM_BASE = 0x02000000
local PLATINUM_PARTY_POINTER_ADDRESS = 0x02101D2C
local PLATINUM_PARTY_COUNT_OFFSET = 0xD090
local PLATINUM_PARTY_RECORDS_OFFSET = 0xD094
local PARTY_POKEMON_SIZE = 236
local PARTY_BYTES = 4 + (6 * PARTY_POKEMON_SIZE)
local PLATINUM_SAVE_DATA_STRUCT_TO_BODY_OFFSET = 0x14
local PLATINUM_SAVE_DATA_BODY_SIZE = 0x20000
local PLATINUM_SAVE_DATA_BODY_PAGE_INFO_OFFSET = 0x20010
local SAVE_PAGE_INFO_SIZE = 0x10
local PC_BOXES_PAGE_ID = 37
local PC_BOXES_PAGE_SIZE = 0x121D0
local PC_BOXES_PAGE_BLOCK_ID = 1
local PC_BOXES_COUNT = 18
local PC_BOX_SLOTS = 30
local PC_BOX_RECORD_SIZE = 0x88
local PC_BOX_RECORDS_OFFSET = 4
local PC_BOX_RECORDS_BYTES = PC_BOXES_COUNT * PC_BOX_SLOTS * PC_BOX_RECORD_SIZE
local PC_SAVE_COPY_RECORD_OFFSETS = {0x0C104, 0x4C104}
local PC_SAVE_HEADER_BYTES = 4
local PC_SAVE_SEGMENT_PREFIX_BYTES = 4
local PC_SAVE_SEGMENT_BYTES = 8 + PC_BOX_RECORDS_BYTES
local PC_SAVE_TEST_CHUNK_BYTES = 0x1000
local PC_SRAM_SEARCH_CHUNK_BYTES = 0x1000
local PC_BOXES_HEADER_BYTES = 64
local MAIN_RAM_SIZE = 0x400000
local PC_DISCOVERY_CHUNK_BYTES = 0x1000
local PC_DISCOVERY_MIN_CHUNK_BYTES = 0x200
local PC_DISCOVERY_WATCHDOG_MS = 5
local PC_DISCOVERY_RETRY_CACHE_CHUNKS = MAIN_RAM_SIZE / PC_DISCOVERY_MIN_CHUNK_BYTES
local PC_DISCOVERY_RESEND_QUEUE_MAX = 64
local PC_POINTER_SEARCH_CHUNK_BYTES = 0x1000
local PC_POINTER_SEARCH_MAX_HITS = 256
local PC_INSPECTION_BEFORE_BYTES = 0x4000
local PC_INSPECTION_AFTER_BYTES = 0x20000
local BATTLE_BATTLER_SIZE = 0xC0
local BATTLE_BATTLER_OFFSETS = {0x54598, 0x54658, 0x54718, 0x547D8}
local PLAYER_X_OFFSET = 0x001C5AFE
local PLAYER_Y_OFFSET = 0x001C5B02
-- This wall-time lease only detects a lost Python controller; movement timing is frame-based.
local WALK_COMMAND_LEASE_SECONDS = 4.0

local socket = nil
pcall(function()
    socket = require("socket")
end)

local function script_source_identifier()
    if debug ~= nil and debug.getinfo ~= nil then
        local ok, info = pcall(debug.getinfo, 2, "S")
        if ok and info ~= nil and info.source ~= nil then
            return info.source
        end
    end
    return "unavailable"
end

local LUA_SCRIPT_SOURCE = script_source_identifier()

local bizhawk_client_api = _G and _G.client or nil
local client = nil
local function new_lua_run_id()
    return tostring(os.time()) .. "-"
        .. tostring(math.floor(os.clock() * 1000)) .. "-"
        .. tostring(math.random(100000, 999999))
end
-- BizHawk/NLua limits a chunk to 200 locals; keep subsystem state and helpers in tables.
-- Begin state namespaces
local TransportState = {
    fallback_limit_warned = false,
    last_connect_attempt = -999999,
    pending_tcp_line = nil,
    pending_tcp_offset = 1,
    queued_tcp_lines = {},
    last_domain_error = nil,
    warned_null_core = false,
    command_buffer = "",
    last_command_file_poll_frame = -COMMAND_FILE_POLL_INTERVAL,
}
local PCState = {
    pc_storage_scan_requested = false,
    pc_storage_discovery_requested = false,
    pc_storage_discovery_anchor_address = nil,
    pc_storage_discovery_expected_identity = nil,
    pc_storage_inspection_requested = false,
    pc_storage_inspection_anchor_address = nil,
    pc_pokemon_search_requested = nil,
    pc_pokemon_search = nil,
    pc_pokemon_search_waiting_ack = nil,
    pc_structure_pointer_search_requested = nil,
    pc_structure_pointer_search = nil,
    pc_storage_discovery_cancel_requested = false,
    pc_save_offset_test_requested = false,
    pc_save_offset_test = nil,
    pc_save_offset_test_sequence = 0,
    pc_sram_search_requested = false,
    pc_sram_search = nil,
    pc_sram_search_sequence = 0,
    pc_discovery_scan = nil,
    pc_discovery_retry_scan = nil,
    pc_discovery_resend_queue = {},
    pc_discovery_resend_queued = {},
    pc_discovery_scan_sequence = 0,
    session_pc_first_record_address = nil,
    session_pc_run_id = nil,
    session_pc_invalid_scans = 0,
    session_pc_expected_species_id = nil,
}
local SessionState = {
    run_id = new_lua_run_id(),
    session_rom_identity = nil,
    session_had_connection = false,
    session_was_connected = false,
}
local CoordinateState = {
    coordinate_scan = nil,
    coordinate_preview = nil,
    previous_preview_x = nil,
    previous_preview_y = nil,
    previous_player_x = nil,
    previous_player_y = nil,
    friendship_walk = {
        enabled = false,
        direction = nil,
        mode = nil,
        status = "Idle",
        pause_reason = nil,
        last_command_time = 0,
        injected_direction = nil,
        b_injected = false,
        pending_direction = nil,
        reversal_until_frame = nil,
        ack_sequence = nil,
        ack_frame = nil,
        ack_action = nil,
        release_pending = false,
    },
}
local SaveRAMDiag = {}
local SRAMDiag = {}
local JSON = {}
-- End state namespaces
local send_line
local write_fallback
local byte_hex = {}
for value = 0, 255 do
    byte_hex[value] = string.format("%02X", value)
end

function JSON.json_escape(value)
    value = tostring(value or "")
    value = value:gsub("\\", "\\\\")
    value = value:gsub("\"", "\\\"")
    value = value:gsub("\b", "\\b")
    value = value:gsub("\f", "\\f")
    value = value:gsub("\n", "\\n")
    value = value:gsub("\r", "\\r")
    value = value:gsub("\t", "\\t")
    return value
end

function JSON.json_value(value)
    local value_type = type(value)
    if value_type == "number" or value_type == "boolean" then
        return tostring(value)
    end
    if value_type == "table" then
        if value.__raw_json ~= nil then
            return value.__raw_json
        end
        local parts = {}
        for index, item in ipairs(value) do
            parts[index] = JSON.json_value(item)
        end
        return "[" .. table.concat(parts, ",") .. "]"
    end
    if value == nil then
        return "null"
    end
    return "\"" .. JSON.json_escape(value) .. "\""
end

function JSON.json_raw(value)
    return {__raw_json = value}
end

function JSON.json_raw_array(items)
    return JSON.json_raw("[" .. table.concat(items or {}, ",") .. "]")
end

function JSON.json_object(fields)
    local parts = {}
    for index, pair in ipairs(fields) do
        parts[index] = "\"" .. pair[1] .. "\":" .. JSON.json_value(pair[2])
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

local function try_call(fn, fallback)
    local ok, result = pcall(fn)
    if ok then
        return result
    end
    return fallback
end

local function get_core_name()
    return try_call(function() return emu.getsystemid() end, "unknown")
end

local function get_rom_session_identity()
    if gameinfo ~= nil then
        local name = try_call(function() return gameinfo.getromname() end, nil)
        local hash = try_call(function() return gameinfo.getromhash() end, nil)
        if name ~= nil or hash ~= nil then
            return tostring(name or "") .. "|" .. tostring(hash or "")
        end
    end
    return tostring(get_core_name())
end

local function clear_session_pc_resolver(reason)
    if PCState.session_pc_first_record_address ~= nil then
        print(string.format(
            "EV Tracker session PC resolver cleared: run_id=%s address=0x%08X reason=%s",
            tostring(PCState.session_pc_run_id),
            PCState.session_pc_first_record_address,
            tostring(reason)
        ))
    end
    PCState.session_pc_first_record_address = nil
    PCState.session_pc_run_id = nil
    PCState.session_pc_invalid_scans = 0
    PCState.session_pc_expected_species_id = nil
end

SessionState.session_rom_identity = get_rom_session_identity()

local function memory_domains_supported()
    local core = string.lower(tostring(get_core_name() or ""))
    if core == "null" or core == "nullhawk" then
        if not TransportState.warned_null_core then
            print("EV Tracker: waiting for a loaded Nintendo DS ROM/core; NullHawk has no memory domains.")
            TransportState.warned_null_core = true
        end
        return false
    end
    TransportState.warned_null_core = false
    return true
end

local function get_frame_count()
    return try_call(function() return emu.framecount() end, 0)
end

local function domain_names()
    if not memory_domains_supported() then
        return {}
    end
    local ok, domains = pcall(function() return memory.getmemorydomainlist() end)
    if not ok then
        if domains ~= TransportState.last_domain_error then
            print("EV Tracker: memory domains unavailable: " .. tostring(domains))
            TransportState.last_domain_error = domains
        end
        return {}
    end
    local names = {}
    for key, value in pairs(domains) do
        if type(key) == "number" then
            table.insert(names, tostring(value))
        else
            table.insert(names, tostring(key))
        end
    end
    table.sort(names)
    return names
end

local function domain_size(name)
    if name == nil or not memory_domains_supported() then
        return nil
    end
    return try_call(function() return memory.getmemorydomainsize(name) end, nil)
end

local function is_readable_domain(name)
    if name == nil or not memory_domains_supported() then
        return false
    end
    local ok = pcall(function()
        memory.usememorydomain(name)
        memory.readbyte(0)
    end)
    return ok
end

local function choose_main_ram_domain(names)
    local readable = {}
    for _, name in ipairs(names) do
        if is_readable_domain(name) then
            local size = domain_size(name)
            table.insert(readable, {name = name, size = size or 0})
        end
    end

    for _, candidate in ipairs(readable) do
        local lowered = string.lower(candidate.name)
        if candidate.size >= 0x400000 and string.find(lowered, "main") and string.find(lowered, "ram") then
            return candidate.name
        end
    end

    for _, candidate in ipairs(readable) do
        if candidate.size == 0x400000 or candidate.size == 0x800000 then
            return candidate.name
        end
    end

    for _, candidate in ipairs(readable) do
        local lowered = string.lower(candidate.name)
        if string.find(lowered, "ram") then
            return candidate.name
        end
    end

    if #readable > 0 then
        return readable[1].name
    end
    return nil
end

local function diagnostic_reads(domain)
    local reads = {}
    if domain == nil then
        return reads
    end

    local addresses = {0x00000000, 0x00000004, 0x00001000, 0x00002000}
    memory.usememorydomain(domain)
    for _, address in ipairs(addresses) do
        local value = try_call(function() return memory.readbyte(address) end, nil)
        table.insert(reads, JSON.json_object({
            {"address", string.format("0x%08X", address)},
            {"value", value},
        }))
    end
    return reads
end

local function address_to_domain_offset(address)
    if address >= MAIN_RAM_BASE then
        return address - MAIN_RAM_BASE
    end
    return address
end

local function read_u32_le(domain, offset)
    if domain == nil or offset == nil then
        return nil
    end
    local ok, value = pcall(function()
        if memory.read_u32_le ~= nil then
            return memory.read_u32_le(offset, domain)
        end
        memory.usememorydomain(domain)
        local b0 = memory.readbyte(offset)
        local b1 = memory.readbyte(offset + 1)
        local b2 = memory.readbyte(offset + 2)
        local b3 = memory.readbyte(offset + 3)
        return b0 + (b1 * 0x100) + (b2 * 0x10000) + (b3 * 0x1000000)
    end)
    if ok then
        return value
    end
    return nil
end

local function read_u16_le(domain, offset)
    if domain == nil or offset == nil then
        return nil
    end
    local ok, value = pcall(function()
        if memory.read_u16_le ~= nil then
            return memory.read_u16_le(offset, domain)
        end
        memory.usememorydomain(domain)
        local b0 = memory.readbyte(offset)
        local b1 = memory.readbyte(offset + 1)
        return b0 + (b1 * 0x100)
    end)
    return ok and value or nil
end

local function read_bytes(domain, offset, length)
    if domain == nil or offset == nil then
        return nil
    end
    if memory.read_bytes_as_binary_string ~= nil then
        local ok, bytes = pcall(memory.read_bytes_as_binary_string, offset, length, domain)
        if ok and bytes ~= nil then
            return bytes
        end
    end
    if memory.read_bytes_as_array ~= nil then
        local ok, bytes = pcall(memory.read_bytes_as_array, offset, length, domain)
        if ok and bytes ~= nil then
            return bytes
        end
    end

    local ok, bytes = pcall(function()
        memory.usememorydomain(domain)
        local result = {}
        for index = 0, length - 1 do
            result[index + 1] = memory.readbyte(offset + index)
        end
        return result
    end)
    return ok and bytes or nil
end

local function read_bytes_hex(domain, offset, length)
    local bytes = read_bytes(domain, offset, length)
    if bytes == nil then
        return nil
    end
    local parts = {}
    for index = 1, length do
        local value
        if type(bytes) == "string" then
            value = string.byte(bytes, index)
        else
            value = bytes[index]
        end
        if value == nil then
            return nil
        end
        parts[index] = byte_hex[value]
    end
    return table.concat(parts)
end

local function bytes_to_hex(bytes, length)
    if bytes == nil then
        return nil
    end
    local parts = {}
    for index = 1, length do
        local value = type(bytes) == "string" and string.byte(bytes, index) or bytes[index]
        if value == nil then
            return nil
        end
        parts[index] = byte_hex[value]
    end
    return table.concat(parts)
end

local function bytes_to_hex_range(bytes, start_index, length)
    if bytes == nil then
        return nil
    end
    local parts = {}
    for index = 0, length - 1 do
        local source_index = start_index + index
        local value = type(bytes) == "string" and string.byte(bytes, source_index) or bytes[source_index]
        if value == nil then
            return nil
        end
        parts[index + 1] = byte_hex[value]
    end
    return table.concat(parts)
end

local function bulk_byte(bytes, index)
    return type(bytes) == "string" and string.byte(bytes, index) or bytes[index]
end

local function bulk_u16_le(bytes, index)
    local lo = bulk_byte(bytes, index)
    local hi = bulk_byte(bytes, index + 1)
    if lo == nil or hi == nil then
        return nil
    end
    return lo + (hi * 0x100)
end

local function bulk_u32_le(bytes, index)
    local lo = bulk_u16_le(bytes, index)
    local hi = bulk_u16_le(bytes, index + 2)
    if lo == nil or hi == nil then
        return nil
    end
    return lo + (hi * 0x10000)
end

local function xor16(left, right)
    local result = 0
    local bit_value = 1
    for _ = 0, 15 do
        local left_bit = left % 2
        local right_bit = right % 2
        if left_bit ~= right_bit then
            result = result + bit_value
        end
        left = math.floor(left / 2)
        right = math.floor(right / 2)
        bit_value = bit_value * 2
    end
    return result
end

local function gen4_prng_next(seed)
    local lo = seed % 0x10000
    local hi = math.floor(seed / 0x10000)
    local lo_product = (0x4E6D * lo) + 0x6073
    local next_lo = lo_product % 0x10000
    local carry = math.floor(lo_product / 0x10000)
    local next_hi = ((0x41C6 * lo) + (0x4E6D * hi) + carry) % 0x10000
    return (next_hi * 0x10000) + next_lo, next_hi
end

local read_bytes_bulk
local main_ram_address_is_valid
local read_bytes_hex_at_address

local GEN4_BLOCK_ORDERS = {
    "ABCD", "ABDC", "ACBD", "ACDB", "ADBC", "ADCB",
    "BACD", "BADC", "BCAD", "BCDA", "BDAC", "BDCA",
    "CABD", "CADB", "CBAD", "CBDA", "CDAB", "CDBA",
    "DABC", "DACB", "DBAC", "DBCA", "DCAB", "DCBA",
}

local function boxed_record_checksum_valid(bytes, record_start)
    local pid = bulk_u32_le(bytes, record_start)
    local checksum = bulk_u16_le(bytes, record_start + 6)
    if pid == nil or checksum == nil then
        return false, nil
    end
    local shuffle_index = (math.floor(pid / 0x2000) % 32) % 24
    local order = GEN4_BLOCK_ORDERS[shuffle_index + 1]
    local decrypted_words = {}
    local seed = checksum
    for encrypted_block = 0, 3 do
        local block_name = order:sub(encrypted_block + 1, encrypted_block + 1)
        local source_block = string.byte(block_name) - string.byte("A")
        for word_index = 0, 15 do
            local rng_word
            seed, rng_word = gen4_prng_next(seed)
            local encrypted_word = bulk_u16_le(
                bytes,
                record_start + 8 + (encrypted_block * 32) + (word_index * 2)
            )
            if encrypted_word == nil then
                return false, nil, checksum, nil, shuffle_index
            end
            decrypted_words[(source_block * 16) + word_index + 1] = xor16(encrypted_word, rng_word)
        end
    end
    local calculated = 0
    for index = 1, 64 do
        local word = decrypted_words[index]
        if word == nil then
            return false, nil, checksum, nil, shuffle_index
        end
        calculated = (calculated + word) % 0x10000
    end
    local species_id = decrypted_words[1]
    return calculated == checksum and species_id ~= nil and species_id >= 1 and species_id <= 493,
        species_id, checksum, calculated, shuffle_index
end

local function score_pc_records(bytes)
    if bytes == nil then
        return 0, 0
    end
    local non_empty = 0
    local checksum_valid = 0
    for record_index = 0, (PC_BOXES_COUNT * PC_BOX_SLOTS) - 1 do
        local record_start = (record_index * PC_BOX_RECORD_SIZE) + 1
        local empty_header = true
        for byte_index = 0, 7 do
            local value = bulk_byte(bytes, record_start + byte_index)
            if value == nil or value ~= 0 then
                empty_header = false
                break
            end
        end
        if not empty_header then
            non_empty = non_empty + 1
            local valid = boxed_record_checksum_valid(bytes, record_start)
            if valid then
                checksum_valid = checksum_valid + 1
            end
        end
    end
    return non_empty, checksum_valid
end


read_bytes_bulk = function(domain, offset, length)
    if domain == nil or offset == nil then
        return nil
    end
    if memory.read_bytes_as_binary_string ~= nil then
        local ok, bytes = pcall(memory.read_bytes_as_binary_string, offset, length, domain)
        if ok and bytes ~= nil then
            return bytes
        end
    end
    if memory.read_bytes_as_array ~= nil then
        local ok, bytes = pcall(memory.read_bytes_as_array, offset, length, domain)
        if ok and bytes ~= nil then
            return bytes
        end
    end
    return nil
end

main_ram_address_is_valid = function(address, length)
    if address == nil or length == nil then
        return false
    end
    return address >= MAIN_RAM_BASE and address + length <= MAIN_RAM_BASE + MAIN_RAM_SIZE
end

local function pc_discovery_event_line(fields, frame)
    fields[#fields + 1] = {"lua_build_id", LUA_BUILD_ID}
    fields[#fields + 1] = {"lua_game_id", LUA_GAME_ID}
    fields[#fields + 1] = {"frame", frame}
    return JSON.json_object(fields)
end

local function emit_pc_discovery_event(fields, frame)
    return send_line(pc_discovery_event_line(fields, frame), frame)
end

function SaveRAMDiag.pc_save_offset_test_event(fields, frame)
    fields[#fields + 1] = {"lua_build_id", LUA_BUILD_ID}
    fields[#fields + 1] = {"lua_game_id", LUA_GAME_ID}
    fields[#fields + 1] = {"lua_game_version", LUA_GAME_VERSION}
    fields[#fields + 1] = {"lua_script_source", LUA_SCRIPT_SOURCE}
    fields[#fields + 1] = {"lua_run_id", SessionState.run_id}
    fields[#fields + 1] = {"lua_rom_name", try_call(function() return gameinfo.getromname() end, nil)}
    fields[#fields + 1] = {"lua_rom_hash", try_call(function() return gameinfo.getromhash() end, nil)}
    fields[#fields + 1] = {"lua_system_id", try_call(function() return emu.getsystemid() end, nil)}
    local rom_path = nil
    local rom_path_api = nil
    for _, candidate in ipairs({
        {gameinfo, "gameinfo", "getrompath"},
        {gameinfo, "gameinfo", "getromfilepath"},
        {bizhawk_client_api, "client", "getrompath"},
        {bizhawk_client_api, "client", "getromfilepath"},
        {emu, "emu", "getrompath"},
        {emu, "emu", "getromfilepath"},
    }) do
        local owner = candidate[1]
        local api_name = candidate[2]
        local method = candidate[3]
        if owner ~= nil and type(owner[method]) == "function" then
            local value = try_call(function() return owner[method]() end, nil)
            if value ~= nil and tostring(value) ~= "" then
                rom_path = tostring(value)
                rom_path_api = api_name .. "." .. method
                break
            end
        end
    end
    fields[#fields + 1] = {"lua_rom_path", rom_path}
    fields[#fields + 1] = {"lua_rom_path_api", rom_path_api or "not exposed"}
    local save_path = nil
    local config = bizhawk_client_api ~= nil
        and try_call(function() return bizhawk_client_api.getconfig() end, nil)
        or nil
    local path_entries = type(config) == "table" and (config.Paths or config.PathEntries) or nil
    if type(path_entries) == "table" then
        for _, entry in pairs(path_entries) do
            if type(entry) == "table"
                and string.lower(tostring(entry.System or "")) == "nds"
                and string.find(string.lower(tostring(entry.Type or entry.Name or "")), "save") ~= nil
            then
                save_path = entry.Path
                break
            end
        end
    end
    fields[#fields + 1] = {"lua_configured_save_ram_path", save_path}
    fields[#fields + 1] = {"frame", frame}
    return send_line(JSON.json_object(fields), frame)
end

function SaveRAMDiag.is_save_memory_domain(name)
    local lowered = string.lower(tostring(name or ""))
    return string.find(lowered, "save") ~= nil
        or string.find(lowered, "sram") ~= nil
        or string.find(lowered, "flash") ~= nil
        or (string.find(lowered, "cart") ~= nil
            and (string.find(lowered, "ram") ~= nil
                or string.find(lowered, "save") ~= nil
                or string.find(lowered, "memory") ~= nil))
end

function SaveRAMDiag.fail_pc_save_offset_test(test_id, message, frame)
    SaveRAMDiag.pc_save_offset_test_event({
        {"type", "pc_save_offset_test_failed"},
        {"test_id", test_id},
        {"error", message},
    }, frame)
    PCState.pc_save_offset_test = nil
    print("EV Tracker save-offset diagnostic failed: " .. tostring(message))
end

function SaveRAMDiag.start_pc_save_offset_test(frame)
    if PCState.pc_save_offset_test ~= nil then
        SaveRAMDiag.fail_pc_save_offset_test(nil, "A save-offset diagnostic is already active.", frame)
        return
    end

    PCState.pc_save_offset_test_sequence = PCState.pc_save_offset_test_sequence + 1
    local test_id = SessionState.run_id .. "-save-pc-" .. tostring(PCState.pc_save_offset_test_sequence)
    local catalog = {}
    local targets = {}
    local names = domain_names()
    for _, name in ipairs(names) do
        local size = domain_size(name)
        local readable = is_readable_domain(name)
        local save_like = SaveRAMDiag.is_save_memory_domain(name)
        local eligible_copy_count = 0
        if save_like and readable and size ~= nil then
            for _, record_offset in ipairs(PC_SAVE_COPY_RECORD_OFFSETS) do
			local segment_offset = record_offset - PC_SAVE_SEGMENT_PREFIX_BYTES
                local fits = segment_offset >= 0
                    and segment_offset + PC_SAVE_SEGMENT_BYTES <= size
                if fits then
                    eligible_copy_count = eligible_copy_count + 1
                    table.insert(targets, {
                        domain = name,
                        domain_size = size,
                        record_offset = record_offset,
                        segment_offset = segment_offset,
                        segment_bytes = PC_SAVE_SEGMENT_BYTES,
                    })
                end
            end
        end
        table.insert(catalog, JSON.json_object({
            {"name", name},
            {"size", size},
            {"readable", readable},
            {"save_like", save_like},
            {"eligible_copy_count", eligible_copy_count},
        }))
    end

    if #targets == 0 then
        local saveram_function = bizhawk_client_api ~= nil
            and try_call(function() return bizhawk_client_api.saveram end, nil)
            or nil
        local save_ram_api_available = type(saveram_function) == "function"
        local flush_ok = false
        local flush_error = nil
        if save_ram_api_available then
            flush_ok, flush_error = pcall(saveram_function)
        end
        SaveRAMDiag.pc_save_offset_test_event({
            {"type", "pc_save_offset_test_file_capture"},
            {"test_id", test_id},
            {"mode", "external_file"},
            {"domains", JSON.json_raw_array(catalog)},
            {"save_ram_api_available", save_ram_api_available},
            {"save_ram_flush_call_ok", flush_ok},
            {"save_ram_flush_error", flush_error},
            {"save_ram_flush_semantics", "flushes emulator SaveRAM buffer to disk; does not invoke an in-game save"},
            {"save_copy_record_offsets", {"0x0C104", "0x4C104"}},
            {"record_size", PC_BOX_RECORD_SIZE},
            {"box_count", PC_BOXES_COUNT},
            {"slots_per_box", PC_BOX_SLOTS},
        }, frame)
        print(string.format(
            "EV Tracker SaveRAM file capture: test=%s api=%s flush_ok=%s",
            test_id,
            tostring(save_ram_api_available),
            tostring(flush_ok)
        ))
        return
    end

    local target_json = {}
    local total_bytes = 0
    for index, target in ipairs(targets) do
        target_json[index] = JSON.json_object({
            {"domain", target.domain},
            {"domain_size", target.domain_size},
            {"record_offset", string.format("0x%X", target.record_offset)},
            {"segment_offset", string.format("0x%X", target.segment_offset)},
            {"segment_bytes", target.segment_bytes},
        })
        total_bytes = total_bytes + target.segment_bytes
    end

    PCState.pc_save_offset_test = {
        test_id = test_id,
        targets = targets,
        target_index = 1,
        byte_offset = 0,
        chunk_index = 0,
        bytes_sent = 0,
        total_bytes = total_bytes,
    }
    SaveRAMDiag.pc_save_offset_test_event({
        {"type", "pc_save_offset_test_started"},
        {"test_id", test_id},
        {"domains", JSON.json_raw_array(catalog)},
        {"targets", JSON.json_raw_array(target_json)},
        {"segment_count", #targets},
        {"segment_bytes", PC_SAVE_SEGMENT_BYTES},
        {"total_bytes", total_bytes},
        {"record_size", PC_BOX_RECORD_SIZE},
        {"box_count", PC_BOXES_COUNT},
        {"slots_per_box", PC_BOX_SLOTS},
        {"save_copy_record_offsets", {"0x0C104", "0x4C104"}},
    }, frame)
    print(string.format(
        "EV Tracker save-offset diagnostic started: test=%s domains=%d targets=%d bytes=%d",
        test_id,
        #catalog,
        #targets,
        total_bytes
    ))
end

function SaveRAMDiag.advance_pc_save_offset_test(frame)
    local test = PCState.pc_save_offset_test
    if test == nil then
        return
    end
    local target = test.targets[test.target_index]
    if target == nil then
        SaveRAMDiag.pc_save_offset_test_event({
            {"type", "pc_save_offset_test_end"},
            {"test_id", test.test_id},
            {"segment_count", #test.targets},
            {"total_bytes", test.total_bytes},
            {"bytes_sent", test.bytes_sent},
        }, frame)
        print(string.format(
            "EV Tracker save-offset diagnostic complete: test=%s segments=%d bytes=%d",
            test.test_id,
            #test.targets,
            test.bytes_sent
        ))
        PCState.pc_save_offset_test = nil
        return
    end

    local amount = math.min(
        PC_SAVE_TEST_CHUNK_BYTES,
        target.segment_bytes - test.byte_offset
    )
    local bytes = read_bytes_bulk(
        target.domain,
        target.segment_offset + test.byte_offset,
        amount
    )
    local data_hex = bytes_to_hex(bytes, amount)
    if data_hex == nil then
        SaveRAMDiag.fail_pc_save_offset_test(
            test.test_id,
            string.format(
                "Could not read %s at domain offset 0x%X.",
                target.domain,
                target.segment_offset + test.byte_offset
            ),
            frame
        )
        return
    end

    SaveRAMDiag.pc_save_offset_test_event({
        {"type", "pc_save_offset_test_chunk"},
        {"test_id", test.test_id},
        {"domain", target.domain},
        {"record_offset", target.record_offset},
        {"segment_offset", test.byte_offset},
        {"chunk_index", test.chunk_index},
        {"byte_count", amount},
        {"data_encoding", "hex"},
        {"data", data_hex},
    }, frame)
    test.byte_offset = test.byte_offset + amount
    test.chunk_index = test.chunk_index + 1
    test.bytes_sent = test.bytes_sent + amount
    if test.byte_offset >= target.segment_bytes then
        test.target_index = test.target_index + 1
        test.byte_offset = 0
        test.chunk_index = 0
    end
end

function SRAMDiag.pc_sram_search_event(fields, frame)
    fields[#fields + 1] = {"lua_build_id", LUA_BUILD_ID}
    fields[#fields + 1] = {"lua_game_id", LUA_GAME_ID}
    fields[#fields + 1] = {"lua_game_version", LUA_GAME_VERSION}
    fields[#fields + 1] = {"lua_run_id", SessionState.run_id}
    fields[#fields + 1] = {"frame", frame}
    return send_line(JSON.json_object(fields), frame)
end

function SRAMDiag.find_sram_domain()
    local names = domain_names()
    local candidates = {}
    for _, name in ipairs(names) do
        if string.find(string.lower(name), "sram", 1, true) ~= nil
            and is_readable_domain(name) then
            table.insert(candidates, {name = name, size = domain_size(name) or 0})
        end
    end
    table.sort(candidates, function(left, right)
        local left_exact = string.lower(left.name) == "sram"
        local right_exact = string.lower(right.name) == "sram"
        if left_exact ~= right_exact then
            return left_exact
        end
        return left.name < right.name
    end)
    return candidates[1], names
end

function SRAMDiag.fail_pc_sram_search(scan_id, message, frame)
    SRAMDiag.pc_sram_search_event({
        {"type", "pc_sram_search_failed"},
        {"scan_id", scan_id},
        {"error", message},
    }, frame)
    PCState.pc_sram_search = nil
    print("EV Tracker live SRAM Pokemon search failed: " .. tostring(message))
end

function SRAMDiag.start_pc_sram_search(frame)
    if PCState.pc_sram_search ~= nil then
        SRAMDiag.fail_pc_sram_search(nil, "A live SRAM search is already active.", frame)
        return
    end
    local candidate, names = SRAMDiag.find_sram_domain()
    local domain_catalog = {}
    local selected_eligible_copies = 0
    for _, name in ipairs(names) do
        local size = domain_size(name)
        local readable = is_readable_domain(name)
        local save_like = SaveRAMDiag.is_save_memory_domain(name)
        local eligible_copy_count = 0
        if save_like and readable and size ~= nil then
            for _, record_offset in ipairs(PC_SAVE_COPY_RECORD_OFFSETS) do
                local segment_offset = record_offset - PC_SAVE_SEGMENT_PREFIX_BYTES
                if segment_offset >= 0
                    and segment_offset + PC_SAVE_SEGMENT_BYTES <= size then
                    eligible_copy_count = eligible_copy_count + 1
                end
            end
        end
        if candidate ~= nil and name == candidate.name then
            selected_eligible_copies = eligible_copy_count
        end
        table.insert(domain_catalog, JSON.json_object({
            {"name", name},
            {"size", size},
            {"readable", readable},
            {"save_like", save_like},
            {"eligible_save_copy_count", eligible_copy_count},
        }))
    end
    if candidate == nil then
        SRAMDiag.pc_sram_search_event({
            {"type", "pc_sram_search_failed"},
            {"scan_id", SessionState.run_id .. "-sram-search-unavailable"},
            {"error", "No readable BizHawk SRAM memory domain was found."},
            {"domains", JSON.json_raw_array(domain_catalog)},
        }, frame)
        return
    end
    if candidate.size <= 0 or candidate.size > 0x1000000 then
        SRAMDiag.pc_sram_search_event({
            {"type", "pc_sram_search_failed"},
            {"scan_id", SessionState.run_id .. "-sram-search-invalid-size"},
            {"error", "SRAM domain size is invalid or exceeds the diagnostic limit."},
            {"domain", candidate.name},
            {"domain_size", candidate.size},
        }, frame)
        return
    end
    PCState.pc_sram_search_sequence = PCState.pc_sram_search_sequence + 1
    local scan_id = SessionState.run_id .. "-sram-search-" .. tostring(PCState.pc_sram_search_sequence)
    local chunk_count = math.floor((candidate.size + PC_SRAM_SEARCH_CHUNK_BYTES - 1)
        / PC_SRAM_SEARCH_CHUNK_BYTES)
    PCState.pc_sram_search = {
        scan_id = scan_id,
        domain = candidate.name,
        total_bytes = candidate.size,
        chunk_count = chunk_count,
        chunk_index = 0,
        bytes_scanned = 0,
    }
    SRAMDiag.pc_sram_search_event({
        {"type", "pc_sram_search_started"},
        {"scan_id", scan_id},
        {"domain", candidate.name},
        {"domain_size", candidate.size},
        {"chunk_bytes", PC_SRAM_SEARCH_CHUNK_BYTES},
        {"chunk_count", chunk_count},
        {"domains", JSON.json_raw_array(domain_catalog)},
        {"eligible_save_copy_count", selected_eligible_copies},
        {"run_id", SessionState.run_id},
    }, frame)
    print(string.format(
        "EV Tracker live SRAM search started: scan=%s domain=%s size=0x%X chunks=%d",
        scan_id,
        candidate.name,
        candidate.size,
        chunk_count
    ))
end

function SRAMDiag.advance_pc_sram_search(frame)
    local scan = PCState.pc_sram_search
    if scan == nil then
        return
    end
    if scan.chunk_index >= scan.chunk_count then
        SRAMDiag.pc_sram_search_event({
            {"type", "pc_sram_search_complete"},
            {"scan_id", scan.scan_id},
            {"domain", scan.domain},
            {"chunk_count", scan.chunk_count},
            {"total_bytes", scan.total_bytes},
            {"bytes_scanned", scan.bytes_scanned},
        }, frame)
        print(string.format(
            "EV Tracker live SRAM search complete: scan=%s bytes=%d chunks=%d",
            scan.scan_id,
            scan.bytes_scanned,
            scan.chunk_count
        ))
        PCState.pc_sram_search = nil
        return
    end

    local offset = scan.chunk_index * PC_SRAM_SEARCH_CHUNK_BYTES
    local amount = math.min(PC_SRAM_SEARCH_CHUNK_BYTES, scan.total_bytes - offset)
    local bytes = read_bytes_bulk(scan.domain, offset, amount)
    local encoded = bytes_to_hex(bytes, amount)
    if encoded == nil then
        SRAMDiag.fail_pc_sram_search(
            scan.scan_id,
            string.format("Could not read SRAM domain %s at offset 0x%X.", scan.domain, offset),
            frame
        )
        return
    end
    SRAMDiag.pc_sram_search_event({
        {"type", "pc_sram_search_chunk"},
        {"scan_id", scan.scan_id},
        {"domain", scan.domain},
        {"chunk_index", scan.chunk_index},
        {"offset", offset},
        {"byte_count", amount},
        {"data_encoding", "hex"},
        {"data", encoded},
        {"bytes_scanned", scan.bytes_scanned + amount},
        {"total_bytes", scan.total_bytes},
    }, frame)
    scan.chunk_index = scan.chunk_index + 1
    scan.bytes_scanned = scan.bytes_scanned + amount
    if scan.chunk_index % 16 == 0 then
        print(string.format(
            "EV Tracker live SRAM search progress: scan=%s chunk=%d/%d bytes=%d/%d",
            scan.scan_id,
            scan.chunk_index,
            scan.chunk_count,
            scan.bytes_scanned,
            scan.total_bytes
        ))
    end
end

local function pc_discovery_fail(scan_id, mode, anchor_address, message, frame, extra_fields)
    local fields = {
        {"type", "pc_discovery_failed"},
        {"scan_id", scan_id},
        {"mode", mode},
        {"anchor_address", anchor_address and string.format("0x%08X", anchor_address) or nil},
        {"error", message},
    }
    if extra_fields ~= nil then
        for _, field in ipairs(extra_fields) do
            fields[#fields + 1] = field
        end
    end
    emit_pc_discovery_event(fields, frame)
    print("EV Tracker PC discovery failed: " .. tostring(message))
end

local function start_pc_discovery(
	frame, domain, anchor_address, inspection_address, expected_identity
)
    PCState.pc_discovery_scan_sequence = PCState.pc_discovery_scan_sequence + 1
    local scan_id = SessionState.run_id .. "-pc-" .. tostring(PCState.pc_discovery_scan_sequence)
    local mode = expected_identity and "session_layout"
        or (inspection_address ~= nil and "inspect"
        or (anchor_address ~= nil and "anchor" or "broad"))
    local reported_anchor_address = inspection_address or anchor_address
    if PCState.pc_pokemon_search ~= nil or PCState.pc_structure_pointer_search ~= nil
        or PCState.pc_structure_pointer_search_requested ~= nil then
        pc_discovery_fail(
            scan_id,
            mode,
            reported_anchor_address,
            "Another PC RAM diagnostic is already active.",
            frame
        )
        return
    end
    if domain == nil then
        pc_discovery_fail(scan_id, mode, reported_anchor_address, "Nintendo DS Main RAM domain is unavailable.", frame)
        return
    end

    local runtime_base_address = read_u32_le(
        domain,
        address_to_domain_offset(PLATINUM_PARTY_POINTER_ADDRESS)
    )
    local start_address = MAIN_RAM_BASE
    local total_bytes = MAIN_RAM_SIZE
    local anchor_data_offset = 0
    if anchor_address ~= nil then
        start_address = anchor_address - 0x400
        total_bytes = 0x400 + PC_BOX_RECORDS_BYTES + 0x400
        anchor_data_offset = 0x400
        if not main_ram_address_is_valid(start_address, total_bytes) then
            pc_discovery_fail(
                scan_id,
                mode,
                anchor_address,
                "Anchor plus its 540-slot region and nearby range are outside Main RAM.",
                frame
            )
            return
        end
    elseif inspection_address ~= nil then
        start_address = math.max(
            MAIN_RAM_BASE,
            inspection_address - PC_INSPECTION_BEFORE_BYTES
        )
        local end_address = math.min(
            MAIN_RAM_BASE + MAIN_RAM_SIZE,
            inspection_address + PC_INSPECTION_AFTER_BYTES
        )
        total_bytes = end_address - start_address
        anchor_data_offset = inspection_address - start_address
        if total_bytes <= 0 or not main_ram_address_is_valid(start_address, total_bytes) then
            pc_discovery_fail(
                scan_id,
                mode,
                inspection_address,
                "Pokemon anchor neighborhood is outside Main RAM.",
                frame
            )
            return
        end
    end

    local party_records_address = nil
    local party_count = 0
    local party_range_valid = false
    if main_ram_address_is_valid(runtime_base_address, PLATINUM_PARTY_RECORDS_OFFSET + PARTY_BYTES) then
        local count = read_u32_le(
            domain,
            address_to_domain_offset(runtime_base_address + PLATINUM_PARTY_COUNT_OFFSET)
        )
        if count ~= nil and count >= 0 and count <= 6 then
            local records_address = runtime_base_address + PLATINUM_PARTY_RECORDS_OFFSET
            if main_ram_address_is_valid(records_address, count * PARTY_POKEMON_SIZE) then
                party_records_address = records_address
                party_count = count
                party_range_valid = true
            end
        end
    else
        runtime_base_address = nil
    end

    PCState.pc_discovery_scan = {
        scan_id = scan_id,
        mode = mode,
        start_address = start_address,
        total_bytes = total_bytes,
        cursor = 0,
        next_chunk_index = 0,
        chunk_cache = {},
        chunk_cache_order = {},
        chunk_bytes = PC_DISCOVERY_CHUNK_BYTES,
        started_frame = frame,
        started_clock = os.clock(),
        anchor_address = reported_anchor_address,
        anchor_data_offset = anchor_data_offset,
        runtime_base_address = runtime_base_address,
        expected_identity = expected_identity,
    }
    PCState.pc_discovery_retry_scan = PCState.pc_discovery_scan
    PCState.pc_discovery_resend_queue = {}
    PCState.pc_discovery_resend_queued = {}
    print(string.format(
        "EV Tracker PC discovery scan started: id=%s mode=%s bytes=%d anchor=%s frame=%d",
        scan_id,
        mode,
        total_bytes,
        reported_anchor_address and string.format("0x%08X", reported_anchor_address) or "none",
        frame
    ))
    emit_pc_discovery_event({
        {"type", "pc_discovery_started"},
        {"scan_id", scan_id},
        {"lua_run_id", SessionState.run_id},
        {"mode", mode},
        {"start_address", string.format("0x%08X", start_address)},
        {"total_bytes", total_bytes},
        {"estimated_chunk_count", math.ceil(total_bytes / PC_DISCOVERY_CHUNK_BYTES)},
        {"anchor_address", reported_anchor_address and string.format("0x%08X", reported_anchor_address) or nil},
        {"anchor_data_offset", anchor_data_offset},
        {"runtime_base_address", runtime_base_address
            and string.format("0x%08X", runtime_base_address) or nil},
        {"party_records_address", party_records_address
            and string.format("0x%08X", party_records_address) or nil},
        {"party_count", party_count},
        {"party_record_size", PARTY_POKEMON_SIZE},
        {"party_range_valid", party_range_valid},
        {"expected_species_id", expected_identity and expected_identity.species_id or nil},
        {"expected_nickname_hex", expected_identity and expected_identity.nickname_hex or nil},
    }, frame)
end

local function cancel_pc_discovery(frame)
    local pending_search = PCState.pc_pokemon_search_requested
    local pending_pointer_search = PCState.pc_structure_pointer_search_requested
    PCState.pc_storage_discovery_requested = false
    PCState.pc_storage_discovery_anchor_address = nil
    PCState.pc_storage_discovery_expected_identity = nil
    PCState.pc_storage_inspection_requested = false
    PCState.pc_storage_inspection_anchor_address = nil
    PCState.pc_pokemon_search_requested = nil
    PCState.pc_structure_pointer_search_requested = nil
    if PCState.pc_structure_pointer_search ~= nil then
        local scan = PCState.pc_structure_pointer_search
        PCState.pc_structure_pointer_search = nil
        emit_pc_discovery_event({
            {"type", "pc_structure_pointer_search_cancelled"},
            {"scan_id", scan.scan_id},
            {"bytes_scanned", scan.cursor},
            {"total_bytes", MAIN_RAM_SIZE},
        }, frame)
        print(string.format(
            "EV Tracker structure pointer search cancelled: id=%s bytes=%d/%d frame=%d",
            scan.scan_id,
            scan.cursor,
            MAIN_RAM_SIZE,
            frame
        ))
        return
    end
    if pending_pointer_search ~= nil then
        emit_pc_discovery_event({
            {"type", "pc_structure_pointer_search_cancelled"},
            {"scan_id", nil},
            {"bytes_scanned", 0},
            {"total_bytes", MAIN_RAM_SIZE},
        }, frame)
        print("EV Tracker pending structure pointer search cancelled before scan start.")
        return
    end
    if PCState.pc_pokemon_search ~= nil then
        local scan = PCState.pc_pokemon_search
        emit_pc_discovery_event({
            {"type", "pc_pokemon_search_cancelled"},
            {"scan_id", scan.scan_id},
            {"anchor_address", string.format("0x%08X", scan.anchor_address)},
            {"bytes_scanned", scan.cursor},
        }, frame)
        print(string.format(
            "EV Tracker Pokemon identity search cancelled: id=%s bytes=%d/%d frame=%d",
            scan.scan_id,
            scan.cursor,
            MAIN_RAM_SIZE,
            frame
        ))
        PCState.pc_pokemon_search = nil
        PCState.pc_pokemon_search_waiting_ack = scan.scan_id
        return
    end
    if pending_search ~= nil then
        emit_pc_discovery_event({
            {"type", "pc_pokemon_search_cancelled"},
            {"scan_id", nil},
            {"anchor_address", string.format("0x%08X", pending_search.anchor_address)},
            {"bytes_scanned", 0},
        }, frame)
        print("EV Tracker pending Pokemon identity search cancelled before scan start.")
        return
    end
    if PCState.pc_discovery_scan == nil then
        PCState.pc_discovery_resend_queue = {}
        PCState.pc_discovery_resend_queued = {}
        PCState.pc_discovery_retry_scan = nil
        print("EV Tracker PC discovery cancel received with no active scan.")
        emit_pc_discovery_event({
            {"type", "pc_discovery_cancelled"},
            {"scan_id", nil},
            {"mode", nil},
            {"bytes_scanned", 0},
        }, frame)
        return
    end
    local scan = PCState.pc_discovery_scan
    print(string.format(
        "EV Tracker PC discovery cancelled: id=%s bytes=%d/%d frame=%d",
        scan.scan_id,
        scan.cursor,
        scan.total_bytes,
        frame
    ))
    emit_pc_discovery_event({
        {"type", "pc_discovery_cancelled"},
        {"scan_id", scan.scan_id},
        {"mode", scan.mode},
        {"bytes_scanned", scan.cursor},
    }, frame)
    PCState.pc_discovery_scan = nil
    PCState.pc_discovery_resend_queue = {}
    PCState.pc_discovery_resend_queued = {}
    PCState.pc_discovery_retry_scan = nil
end

local function read_active_party_range(domain)
    local runtime_base_address = read_u32_le(
        domain,
        address_to_domain_offset(PLATINUM_PARTY_POINTER_ADDRESS)
    )
    local party_records_address = nil
    local party_count = 0
    local party_range_valid = false
    if main_ram_address_is_valid(
        runtime_base_address,
        PLATINUM_PARTY_RECORDS_OFFSET + PARTY_BYTES
    ) then
        local count = read_u32_le(
            domain,
            address_to_domain_offset(runtime_base_address + PLATINUM_PARTY_COUNT_OFFSET)
        )
        if count ~= nil and count >= 0 and count <= 6 then
            local records_address = runtime_base_address + PLATINUM_PARTY_RECORDS_OFFSET
            if main_ram_address_is_valid(
                records_address,
                count * PARTY_POKEMON_SIZE
            ) then
                party_records_address = records_address
                party_count = count
                party_range_valid = true
            end
        end
    else
        runtime_base_address = nil
    end
    return runtime_base_address, party_records_address, party_count, party_range_valid
end

local function start_pc_pokemon_search(frame, domain, identity)
    PCState.pc_discovery_scan_sequence = PCState.pc_discovery_scan_sequence + 1
    local scan_id = SessionState.run_id .. "-pokemon-search-" .. tostring(PCState.pc_discovery_scan_sequence)
    if domain == nil then
        PCState.pc_pokemon_search_waiting_ack = scan_id
        emit_pc_discovery_event({
            {"type", "pc_pokemon_search_failed"},
            {"scan_id", scan_id},
            {"anchor_address", string.format("0x%08X", identity.anchor_address)},
            {"error", "Nintendo DS Main RAM domain is unavailable."},
        }, frame)
        return
    end
    if PCState.pc_discovery_scan ~= nil or PCState.pc_pokemon_search ~= nil
        or PCState.pc_structure_pointer_search ~= nil then
        PCState.pc_pokemon_search_waiting_ack = scan_id
        emit_pc_discovery_event({
            {"type", "pc_pokemon_search_failed"},
            {"scan_id", scan_id},
            {"anchor_address", string.format("0x%08X", identity.anchor_address)},
            {"error", "Another PC RAM diagnostic is already active."},
        }, frame)
        return
    end
    local runtime_base, party_records, party_count, party_valid =
        read_active_party_range(domain)
    PCState.pc_pokemon_search = {
        scan_id = scan_id,
        identity = identity,
        cursor = 0,
        started_frame = frame,
        started_clock = os.clock(),
        last_progress = 0,
        match_count = 0,
        runtime_base_address = runtime_base,
        party_records_address = party_records,
        party_count = party_count,
        party_range_valid = party_valid,
    }
    print(string.format(
        "EV Tracker Pokemon identity search started: id=%s pid=0x%08X checksum=0x%04X species=%d frame=%d",
        scan_id,
        identity.pid,
        identity.checksum,
        identity.species_id,
        frame
    ))
    emit_pc_discovery_event({
        {"type", "pc_pokemon_search_started"},
        {"scan_id", scan_id},
        {"anchor_address", string.format("0x%08X", identity.anchor_address)},
        {"pid", string.format("0x%08X", identity.pid)},
        {"checksum", string.format("0x%04X", identity.checksum)},
        {"species_id", identity.species_id},
        {"total_bytes", MAIN_RAM_SIZE},
        {"party_records_address", party_records
            and string.format("0x%08X", party_records) or nil},
        {"party_count", party_count},
        {"party_record_size", PARTY_POKEMON_SIZE},
        {"party_range_valid", party_valid},
    }, frame)
end

local function advance_pc_pokemon_search(frame, domain)
    local scan = PCState.pc_pokemon_search
    if scan == nil or frame <= scan.started_frame then
        return
    end
    if client ~= nil and (TransportState.pending_tcp_line ~= nil or #TransportState.queued_tcp_lines > 0) then
        return
    end
    if domain == nil then
        PCState.pc_pokemon_search = nil
        PCState.pc_pokemon_search_waiting_ack = scan.scan_id
        emit_pc_discovery_event({
            {"type", "pc_pokemon_search_failed"},
            {"scan_id", scan.scan_id},
            {"anchor_address", string.format("0x%08X", scan.identity.anchor_address)},
            {"error", "Nintendo DS Main RAM domain became unavailable during search."},
        }, frame)
        return
    end

    local started_at = os.clock()
    local candidate_span = math.min(0x1000, MAIN_RAM_SIZE - scan.cursor)
    local last_record_start = MAIN_RAM_SIZE - PC_BOX_RECORD_SIZE
    local readable = math.min(candidate_span + PC_BOX_RECORD_SIZE, MAIN_RAM_SIZE - scan.cursor)
    local bytes = read_bytes_bulk(
        domain,
        address_to_domain_offset(MAIN_RAM_BASE + scan.cursor),
        readable
    )
    if bytes == nil then
        PCState.pc_pokemon_search = nil
        PCState.pc_pokemon_search_waiting_ack = scan.scan_id
        emit_pc_discovery_event({
            {"type", "pc_pokemon_search_failed"},
            {"scan_id", scan.scan_id},
            {"anchor_address", string.format("0x%08X", scan.identity.anchor_address)},
            {"error", "Could not read the next bounded Main RAM search window."},
        }, frame)
        return
    end

    local processed = 0
    local candidate_index = 1
    while processed < candidate_span do
        local candidate_offset = scan.cursor + processed
        if candidate_offset > last_record_start then
            processed = candidate_span
            break
        end
        local pid = bulk_u32_le(bytes, candidate_index)
        local checksum = bulk_u16_le(bytes, candidate_index + 6)
        if pid == scan.identity.pid and checksum == scan.identity.checksum then
            local valid, species_id = boxed_record_checksum_valid(bytes, candidate_index)
            if valid and species_id == scan.identity.species_id then
                local record_hex = bytes_to_hex_range(bytes, candidate_index, PC_BOX_RECORD_SIZE)
                if record_hex ~= nil then
                    scan.match_count = scan.match_count + 1
                    local address = MAIN_RAM_BASE + candidate_offset
                    print(string.format(
                        "EV Tracker Pokemon identity match: id=%s address=0x%08X match=%d",
                        scan.scan_id,
                        address,
                        scan.match_count
                    ))
                    emit_pc_discovery_event({
                        {"type", "pc_pokemon_search_match"},
                        {"scan_id", scan.scan_id},
                        {"address", string.format("0x%08X", address)},
                        {"record_hex", record_hex},
                    }, frame)
                end
            end
        end
        processed = processed + 4
        candidate_index = candidate_index + 4
        if (os.clock() - started_at) * 1000 >= PC_DISCOVERY_WATCHDOG_MS then
            break
        end
    end
    if processed <= 0 then
        processed = math.min(4, MAIN_RAM_SIZE - scan.cursor)
    end
    scan.cursor = math.min(MAIN_RAM_SIZE, scan.cursor + processed)
    local elapsed_ms = (os.clock() - started_at) * 1000
    if scan.cursor >= MAIN_RAM_SIZE then
        PCState.pc_pokemon_search = nil
        PCState.pc_pokemon_search_waiting_ack = scan.scan_id
        print(string.format(
            "EV Tracker Pokemon identity search completed: id=%s matches=%d elapsed=%.3fs",
            scan.scan_id,
            scan.match_count,
            os.clock() - scan.started_clock
        ))
        emit_pc_discovery_event({
            {"type", "pc_pokemon_search_end"},
            {"scan_id", scan.scan_id},
            {"anchor_address", string.format("0x%08X", scan.identity.anchor_address)},
            {"bytes_scanned", scan.cursor},
            {"total_bytes", MAIN_RAM_SIZE},
            {"match_count", scan.match_count},
            {"elapsed_ms", math.floor((os.clock() - scan.started_clock) * 1000 + 0.5)},
        }, frame)
    elseif scan.cursor - scan.last_progress >= 0x10000 then
        scan.last_progress = scan.cursor
        emit_pc_discovery_event({
            {"type", "pc_pokemon_search_progress"},
            {"scan_id", scan.scan_id},
            {"bytes_scanned", scan.cursor},
            {"total_bytes", MAIN_RAM_SIZE},
            {"current_address", string.format("0x%08X", MAIN_RAM_BASE + scan.cursor)},
            {"match_count", scan.match_count},
            {"elapsed_ms", math.floor((os.clock() - scan.started_clock) * 1000 + 0.5)},
        }, frame)
    end
    if elapsed_ms > PC_DISCOVERY_WATCHDOG_MS then
        print(string.format(
            "EV Tracker Pokemon search watchdog: step=%.2fms next_offset=0x%X",
            elapsed_ms,
            scan.cursor
        ))
    end
end

local function start_pc_structure_pointer_search(frame, domain, targets)
    PCState.pc_discovery_scan_sequence = PCState.pc_discovery_scan_sequence + 1
    local scan_id = SessionState.run_id .. "-pc-pointers-" .. tostring(PCState.pc_discovery_scan_sequence)
    if domain == nil then
        emit_pc_discovery_event({
            {"type", "pc_structure_pointer_search_failed"},
            {"scan_id", scan_id},
            {"error", "Nintendo DS Main RAM domain is unavailable."},
        }, frame)
        return
    end
    if PCState.pc_discovery_scan ~= nil or PCState.pc_pokemon_search ~= nil
        or PCState.pc_structure_pointer_search ~= nil then
        emit_pc_discovery_event({
            {"type", "pc_structure_pointer_search_failed"},
            {"scan_id", scan_id},
            {"error", "Another PC RAM diagnostic is already active."},
        }, frame)
        return
    end
    local runtime_base_address = read_u32_le(
        domain,
        address_to_domain_offset(PLATINUM_PARTY_POINTER_ADDRESS)
    )
    if not main_ram_address_is_valid(runtime_base_address, 1) then
        runtime_base_address = nil
    end
    PCState.pc_structure_pointer_search = {
        scan_id = scan_id,
        targets = targets,
        cursor = 0,
        started_frame = frame,
        started_clock = os.clock(),
        last_progress = 0,
        references = {},
        reference_count = 0,
        runtime_base_address = runtime_base_address,
    }
    print(string.format(
        "EV Tracker structure pointer search started: id=%s header=0x%08X " ..
        "first_record=0x%08X frame=%d",
        scan_id,
        targets.header,
        targets.first_record,
        frame
    ))
    emit_pc_discovery_event({
        {"type", "pc_structure_pointer_search_started"},
        {"scan_id", scan_id},
        {"header_address", string.format("0x%08X", targets.header)},
        {"first_record_address", string.format("0x%08X", targets.first_record)},
        {"runtime_pointer_slot", string.format("0x%08X", PLATINUM_PARTY_POINTER_ADDRESS)},
        {"runtime_base_address", runtime_base_address
            and string.format("0x%08X", runtime_base_address) or nil},
        {"header_offset_from_runtime_base", runtime_base_address
            and (targets.header - runtime_base_address) or nil},
        {"first_record_offset_from_runtime_base", runtime_base_address
            and (targets.first_record - runtime_base_address) or nil},
        {"total_bytes", MAIN_RAM_SIZE},
    }, frame)
end

local function advance_pc_structure_pointer_search(frame, domain)
    local scan = PCState.pc_structure_pointer_search
    if scan == nil or frame <= scan.started_frame then
        return
    end
    if client ~= nil and (TransportState.pending_tcp_line ~= nil or #TransportState.queued_tcp_lines > 0) then
        return
    end
    if domain == nil then
        PCState.pc_structure_pointer_search = nil
        emit_pc_discovery_event({
            {"type", "pc_structure_pointer_search_failed"},
            {"scan_id", scan.scan_id},
            {"error", "Nintendo DS Main RAM domain became unavailable during search."},
        }, frame)
        return
    end

    local started_at = os.clock()
    local candidate_span = math.min(PC_POINTER_SEARCH_CHUNK_BYTES, MAIN_RAM_SIZE - scan.cursor)
    local readable = math.min(candidate_span + 3, MAIN_RAM_SIZE - scan.cursor)
    local bytes = read_bytes_bulk(
        domain,
        address_to_domain_offset(MAIN_RAM_BASE + scan.cursor),
        readable
    )
    if bytes == nil then
        PCState.pc_structure_pointer_search = nil
        emit_pc_discovery_event({
            {"type", "pc_structure_pointer_search_failed"},
            {"scan_id", scan.scan_id},
            {"error", "Could not read the next bounded pointer-search chunk."},
        }, frame)
        return
    end

    local processed = 0
    while processed < candidate_span do
        local candidate_offset = scan.cursor + processed
        if candidate_offset + 4 > MAIN_RAM_SIZE then
            processed = candidate_span
            break
        end
        local value = bulk_u32_le(bytes, processed + 1)
        local target_name = value == scan.targets.header and "header"
            or (value == scan.targets.first_record and "first_record" or nil)
        if target_name ~= nil then
            scan.reference_count = scan.reference_count + 1
            if #scan.references < PC_POINTER_SEARCH_MAX_HITS then
                scan.references[#scan.references + 1] = JSON.json_object({
                    {"reference_address", string.format("0x%08X", MAIN_RAM_BASE + candidate_offset)},
                    {"target_address", string.format("0x%08X", value)},
                    {"target", target_name},
                })
            end
        end
        processed = processed + 4
        if (os.clock() - started_at) * 1000 >= PC_DISCOVERY_WATCHDOG_MS then
            break
        end
    end
    if processed <= 0 then
        processed = math.min(4, MAIN_RAM_SIZE - scan.cursor)
    end
    scan.cursor = math.min(MAIN_RAM_SIZE, scan.cursor + processed)
    if scan.cursor >= MAIN_RAM_SIZE then
        PCState.pc_structure_pointer_search = nil
        print(string.format(
            "EV Tracker structure pointer search completed: id=%s refs=%d elapsed=%.3fs",
            scan.scan_id,
            scan.reference_count,
            os.clock() - scan.started_clock
        ))
        emit_pc_discovery_event({
            {"type", "pc_structure_pointer_search_end"},
            {"scan_id", scan.scan_id},
            {"header_address", string.format("0x%08X", scan.targets.header)},
            {"first_record_address", string.format("0x%08X", scan.targets.first_record)},
            {"runtime_pointer_slot", string.format("0x%08X", PLATINUM_PARTY_POINTER_ADDRESS)},
            {"runtime_base_address", scan.runtime_base_address
                and string.format("0x%08X", scan.runtime_base_address) or nil},
            {"header_offset_from_runtime_base", scan.runtime_base_address
                and (scan.targets.header - scan.runtime_base_address) or nil},
            {"first_record_offset_from_runtime_base", scan.runtime_base_address
                and (scan.targets.first_record - scan.runtime_base_address) or nil},
            {"bytes_scanned", scan.cursor},
            {"total_bytes", MAIN_RAM_SIZE},
            {"reference_count", scan.reference_count},
            {"references", JSON.json_raw_array(scan.references)},
            {"elapsed_ms", math.floor((os.clock() - scan.started_clock) * 1000 + 0.5)},
        }, frame)
    elseif scan.cursor - scan.last_progress >= 0x10000 then
        scan.last_progress = scan.cursor
        emit_pc_discovery_event({
            {"type", "pc_structure_pointer_search_progress"},
            {"scan_id", scan.scan_id},
            {"bytes_scanned", scan.cursor},
            {"total_bytes", MAIN_RAM_SIZE},
            {"current_address", string.format("0x%08X", MAIN_RAM_BASE + scan.cursor)},
            {"reference_count", scan.reference_count},
        }, frame)
    end
    local elapsed_ms = (os.clock() - started_at) * 1000
    if elapsed_ms > PC_DISCOVERY_WATCHDOG_MS then
        print(string.format(
            "EV Tracker structure pointer search watchdog: step=%.2fms next_offset=0x%X",
            elapsed_ms,
            scan.cursor
        ))
    end
end

local function advance_pc_discovery(frame, domain)
    local scan = PCState.pc_discovery_scan
    if scan == nil or frame <= scan.started_frame then
        return
    end
    if client ~= nil and (TransportState.pending_tcp_line ~= nil or #TransportState.queued_tcp_lines > 0) then
        return
    end
    if scan.cursor >= scan.total_bytes then
        print(string.format(
            "EV Tracker PC discovery scan completed: id=%s bytes=%d elapsed=%.3fs frame=%d",
            scan.scan_id,
            scan.cursor,
            os.clock() - scan.started_clock,
            frame
        ))
        emit_pc_discovery_event({
            {"type", "pc_discovery_end"},
            {"scan_id", scan.scan_id},
            {"mode", scan.mode},
            {"bytes_scanned", scan.cursor},
            {"chunk_count", scan.next_chunk_index},
            {"total_bytes", scan.cursor},
            {"elapsed_ms", math.floor((os.clock() - scan.started_clock) * 1000 + 0.5)},
        }, frame)
        PCState.pc_discovery_scan = nil
        return
    end
    if domain == nil then
        PCState.pc_discovery_scan = nil
        pc_discovery_fail(scan.scan_id, scan.mode, scan.anchor_address,
            "Nintendo DS Main RAM domain became unavailable during discovery.", frame)
        return
    end

    local started_at = os.clock()
    local max_length = math.min(scan.chunk_bytes, scan.total_bytes - scan.cursor)
    local processed = 0
    local encoded_parts = {}
    repeat
        local part_length = math.min(0x200, max_length - processed)
        local bytes = read_bytes_bulk(
            domain,
            address_to_domain_offset(scan.start_address) + scan.cursor + processed,
            part_length
        )
        if bytes == nil then
            PCState.pc_discovery_scan = nil
            pc_discovery_fail(scan.scan_id, scan.mode, scan.anchor_address,
                "Could not read the next bounded Main RAM discovery chunk.", frame)
            return
        end
        local encoded_part = bytes_to_hex(bytes, part_length)
        if encoded_part == nil then
            PCState.pc_discovery_scan = nil
            pc_discovery_fail(scan.scan_id, scan.mode, scan.anchor_address,
                "Could not encode the Main RAM discovery chunk.", frame)
            return
        end
        encoded_parts[#encoded_parts + 1] = encoded_part
        processed = processed + part_length
    until processed >= max_length
        or (os.clock() - started_at) * 1000 >= PC_DISCOVERY_WATCHDOG_MS

    if processed <= 0 then
        return
    end
    local encoded = table.concat(encoded_parts)
    local chunk_offset = scan.cursor
    local chunk_index = scan.next_chunk_index
    local current_address = scan.start_address + chunk_offset
    scan.cursor = scan.cursor + processed
    scan.next_chunk_index = scan.next_chunk_index + 1
    local elapsed_ms = (os.clock() - started_at) * 1000
    local chunk_line = pc_discovery_event_line({
        {"type", "pc_discovery_chunk"},
        {"scan_id", scan.scan_id},
        {"mode", scan.mode},
        {"chunk_index", chunk_index},
        {"offset", chunk_offset},
        {"byte_count", processed},
        {"current_address", string.format("0x%08X", current_address)},
        {"bytes_scanned", scan.cursor},
        {"total_bytes", scan.total_bytes},
        {"progress_percent", math.floor(scan.cursor * 100 / scan.total_bytes)},
        {"candidate_records_found", 0},
        {"elapsed_ms", math.floor(elapsed_ms + 0.5)},
        {"data_encoding", "hex"},
        {"data", encoded},
    }, frame)
    scan.chunk_cache[chunk_index] = chunk_line
    table.insert(scan.chunk_cache_order, chunk_index)
    if #scan.chunk_cache_order > PC_DISCOVERY_RETRY_CACHE_CHUNKS then
        local expired_index = table.remove(scan.chunk_cache_order, 1)
        scan.chunk_cache[expired_index] = nil
    end
    send_line(chunk_line, frame)
    if chunk_index % 50 == 0 then
        local pending_command_count = #PCState.pc_discovery_resend_queue
        if TransportState.command_buffer ~= "" then
            pending_command_count = pending_command_count + 1
        end
        print(string.format(
            "EV Tracker PC discovery producer: scan_id=%s chunk_index=%d " ..
            "current_address=0x%08X bytes_scanned=%d scan_active=%s " ..
            "pending_command_count=%d resend_queue_length=%d",
            scan.scan_id,
            chunk_index,
            current_address,
            scan.cursor,
            tostring(PCState.pc_discovery_scan == scan),
            pending_command_count,
            #PCState.pc_discovery_resend_queue
        ))
    end
    if elapsed_ms > PC_DISCOVERY_WATCHDOG_MS then
        scan.chunk_bytes = math.max(
            PC_DISCOVERY_MIN_CHUNK_BYTES,
            math.floor(math.min(scan.chunk_bytes, processed) / 2 / 4) * 4
        )
        print(string.format(
            "EV Tracker PC discovery watchdog: chunk took %.2fms; next chunk=%d bytes",
            elapsed_ms,
            scan.chunk_bytes
        ))
    end
end

read_bytes_hex_at_address = function(domain, address, length)
    if not main_ram_address_is_valid(address, length) then
        return nil
    end
    return read_bytes_hex(domain, address_to_domain_offset(address), length)
end

local function read_coordinate(domain, offset, data_type)
    local value = read_u16_le(domain, offset)
    if value ~= nil and data_type == "s16" and value >= 0x8000 then
        value = value - 0x10000
    end
    return value
end

local function player_coordinates(domain)
    return read_coordinate(domain, PLAYER_X_OFFSET, "s16"),
        read_coordinate(domain, PLAYER_Y_OFFSET, "s16")
end

local function battler_record_active(domain, pointer_value, battler_index)
    if domain == nil or pointer_value == nil then
        return false
    end
    local relative_offset = BATTLE_BATTLER_OFFSETS[battler_index + 1]
    if relative_offset == nil then
        return false
    end
    local record_offset = address_to_domain_offset(pointer_value + relative_offset)
    local species = read_u16_le(domain, record_offset)
    local level = try_call(function()
        memory.usememorydomain(domain)
        return memory.readbyte(record_offset + 0x34)
    end, nil)
    return species ~= nil and species >= 1 and species <= 493
        and level ~= nil and level >= 1 and level <= 100
end

local function battle_active_now(domain)
    local pointer_offset = address_to_domain_offset(PLATINUM_PARTY_POINTER_ADDRESS)
    local pointer_value = read_u32_le(domain, pointer_offset)
    if pointer_value == nil then
        return false
    end
    return (battler_record_active(domain, pointer_value, 0)
            and battler_record_active(domain, pointer_value, 1))
        or (battler_record_active(domain, pointer_value, 2)
            and battler_record_active(domain, pointer_value, 3))
end

local function release_walk_input()
    pcall(function()
        joypad.set({Up = false, Down = false, Left = false, Right = false, B = false})
    end)
    CoordinateState.friendship_walk.injected_direction = nil
    CoordinateState.friendship_walk.b_injected = false
    CoordinateState.friendship_walk.pending_direction = nil
    CoordinateState.friendship_walk.reversal_until_frame = nil
    CoordinateState.friendship_walk.release_pending = false
end

local function pause_friendship_walk(reason, status, release_input)
    CoordinateState.friendship_walk.enabled = false
    CoordinateState.friendship_walk.direction = nil
    CoordinateState.friendship_walk.pending_direction = nil
    CoordinateState.friendship_walk.reversal_until_frame = nil
    CoordinateState.friendship_walk.pause_reason = reason
    CoordinateState.friendship_walk.status = status
    CoordinateState.friendship_walk.release_pending = release_input ~= false
end

local function apply_friendship_walk(frame, domain)
    if CoordinateState.friendship_walk.enabled then
        if os.time() - CoordinateState.friendship_walk.last_command_time > WALK_COMMAND_LEASE_SECONDS then
            pause_friendship_walk("command_stale", "Paused — Movement command channel lost", true)
        else
            local x, y = player_coordinates(domain)
            if x == nil or y == nil then
                pause_friendship_walk("coordinates", "Paused — Coordinates unavailable", true)
            elseif battle_active_now(domain) then
                pause_friendship_walk("battle", "Paused — Battle", true)
            end
        end
    end

    if CoordinateState.friendship_walk.release_pending then
        release_walk_input()
    elseif CoordinateState.friendship_walk.enabled then
        if CoordinateState.friendship_walk.pending_direction ~= nil
            and frame >= (CoordinateState.friendship_walk.reversal_until_frame or frame) then
            CoordinateState.friendship_walk.direction = CoordinateState.friendship_walk.pending_direction
            CoordinateState.friendship_walk.pending_direction = nil
            CoordinateState.friendship_walk.reversal_until_frame = nil
            CoordinateState.friendship_walk.status = "Walking " .. CoordinateState.friendship_walk.direction
        end
        local direction = CoordinateState.friendship_walk.direction
        local inputs = {
            Up = direction == "Up",
            Down = direction == "Down",
            Left = direction == "Left",
            Right = direction == "Right",
            B = true,
        }
        local ok = pcall(function() joypad.set(inputs) end)
        if ok then
            CoordinateState.friendship_walk.injected_direction = direction
            CoordinateState.friendship_walk.b_injected = true
        else
            pause_friendship_walk("command", "Paused — Controller unavailable", true)
            release_walk_input()
        end
    end
end

local function party_memory_message(frame, domain)
    local pointer_offset = address_to_domain_offset(PLATINUM_PARTY_POINTER_ADDRESS)
    local pointer_value = read_u32_le(domain, pointer_offset)
    local party_address = nil
    local party_offset = nil
    local party_records_address = nil
    local party_records_offset = nil
    local party_count = nil
    local raw_party = nil
    local candidate_diagnostics = {}
    local battle_battler_fields = {}
    local party_count_valid = false

    if pointer_value ~= nil then
        party_address = pointer_value + PLATINUM_PARTY_COUNT_OFFSET
        party_offset = address_to_domain_offset(party_address)
        party_records_address = pointer_value + PLATINUM_PARTY_RECORDS_OFFSET
        party_records_offset = address_to_domain_offset(party_records_address)
        party_count = read_u32_le(domain, party_offset)
        party_count_valid = party_count ~= nil and party_count >= 0 and party_count <= 6
        raw_party = read_bytes_hex(domain, party_offset, PARTY_BYTES)
        for index, relative_offset in ipairs(BATTLE_BATTLER_OFFSETS) do
            local battler_index = index - 1
            local record_address = pointer_value + relative_offset
            local record_offset = address_to_domain_offset(record_address)
            local raw_record = read_bytes_hex(domain, record_offset, BATTLE_BATTLER_SIZE)
            local prefix = string.format("battle_battler_%d_", battler_index)
            table.insert(battle_battler_fields, {prefix .. "relative_offset", string.format("0x%X", relative_offset)})
            table.insert(battle_battler_fields, {prefix .. "address", string.format("0x%08X", record_address)})
            table.insert(battle_battler_fields, {prefix .. "raw_hex", raw_record})
        end
        candidate_diagnostics[1] = string.format(
            "count relative=0x%X address=0x%08X offset=0x%08X count=%s valid=%s",
            PLATINUM_PARTY_COUNT_OFFSET,
            party_address,
            party_offset,
            tostring(party_count),
            tostring(party_count_valid)
        )
        candidate_diagnostics[2] = string.format(
            "records relative=0x%X address=0x%08X offset=0x%08X stride=0x%X",
            PLATINUM_PARTY_RECORDS_OFFSET,
            party_records_address,
            party_records_offset,
            PARTY_POKEMON_SIZE
        )
    end

    local preview_x = CoordinateState.coordinate_preview and read_coordinate(domain, CoordinateState.coordinate_preview.x_offset, CoordinateState.coordinate_preview.data_type) or nil
    local preview_y = CoordinateState.coordinate_preview and read_coordinate(domain, CoordinateState.coordinate_preview.y_offset, CoordinateState.coordinate_preview.data_type) or nil
    local preview_previous_x = CoordinateState.previous_preview_x
    local preview_previous_y = CoordinateState.previous_preview_y
    local player_x, player_y = player_coordinates(domain)
    local player_delta_x = player_x ~= nil and CoordinateState.previous_player_x ~= nil
        and player_x - CoordinateState.previous_player_x or nil
    local player_delta_y = player_y ~= nil and CoordinateState.previous_player_y ~= nil
        and player_y - CoordinateState.previous_player_y or nil
    local message_fields = {
        {"type", "party_memory"},
        {"run_id", SessionState.run_id},
        {"frame", frame},
        {"core", get_core_name()},
        {"domain", domain},
        {"main_ram_base", string.format("0x%08X", MAIN_RAM_BASE)},
        {"coordinate_preview_x", preview_x},
        {"coordinate_preview_y", preview_y},
        {"coordinate_preview_previous_x", preview_previous_x},
        {"coordinate_preview_previous_y", preview_previous_y},
        {"coordinate_preview_x_offset", CoordinateState.coordinate_preview and string.format("0x%08X", CoordinateState.coordinate_preview.x_offset) or nil},
        {"coordinate_preview_y_offset", CoordinateState.coordinate_preview and string.format("0x%08X", CoordinateState.coordinate_preview.y_offset) or nil},
        {"coordinate_preview_type", CoordinateState.coordinate_preview and CoordinateState.coordinate_preview.data_type or nil},
        {"player_x", player_x},
        {"player_y", player_y},
        {"player_delta_x", player_delta_x},
        {"player_delta_y", player_delta_y},
        {"player_coordinates_validated", true},
        {"player_x_offset", string.format("0x%08X", PLAYER_X_OFFSET)},
        {"player_y_offset", string.format("0x%08X", PLAYER_Y_OFFSET)},
        {"friendship_walk_enabled", CoordinateState.friendship_walk.enabled},
        {"friendship_walk_mode", CoordinateState.friendship_walk.mode},
        {"friendship_walk_direction", CoordinateState.friendship_walk.direction or CoordinateState.friendship_walk.pending_direction},
        {"friendship_walk_requested_direction", CoordinateState.friendship_walk.direction or CoordinateState.friendship_walk.pending_direction},
        {"friendship_walk_injected_direction", CoordinateState.friendship_walk.injected_direction},
        {"friendship_walk_b_injected", CoordinateState.friendship_walk.b_injected},
        {"friendship_walk_reversal_until_frame", CoordinateState.friendship_walk.reversal_until_frame},
        {"friendship_walk_status", CoordinateState.friendship_walk.status},
        {"friendship_walk_pause_reason", CoordinateState.friendship_walk.pause_reason},
        {"friendship_walk_ack_sequence", CoordinateState.friendship_walk.ack_sequence},
        {"friendship_walk_ack_frame", CoordinateState.friendship_walk.ack_frame},
        {"friendship_walk_ack_action", CoordinateState.friendship_walk.ack_action},
        {"pointer_address", string.format("0x%08X", PLATINUM_PARTY_POINTER_ADDRESS)},
        {"pointer_offset", string.format("0x%08X", pointer_offset)},
        {"pointer_value", pointer_value and string.format("0x%08X", pointer_value) or nil},
        {"party_relative_offset", pointer_value and string.format("0x%X", PLATINUM_PARTY_COUNT_OFFSET) or nil},
        {"party_offset_candidates", candidate_diagnostics},
        {"party_address", party_address and string.format("0x%08X", party_address) or nil},
        {"party_offset", party_offset and string.format("0x%08X", party_offset) or nil},
        {"party_records_address", party_records_address and string.format("0x%08X", party_records_address) or nil},
        {"party_records_offset", party_records_offset and string.format("0x%08X", party_records_offset) or nil},
        {"party_count", party_count},
        {"party_count_valid", party_count_valid},
        {"pokemon_size", PARTY_POKEMON_SIZE},
        {"raw_party_hex", raw_party},
    }
    if CoordinateState.coordinate_preview then
        if preview_x ~= nil and preview_y ~= nil then
            CoordinateState.previous_preview_x = preview_x
            CoordinateState.previous_preview_y = preview_y
        end
    else
        CoordinateState.previous_preview_x = nil
        CoordinateState.previous_preview_y = nil
    end
    if player_x ~= nil and player_y ~= nil then
        CoordinateState.previous_player_x = player_x
        CoordinateState.previous_player_y = player_y
    else
        CoordinateState.previous_player_x = nil
        CoordinateState.previous_player_y = nil
    end
    for _, field in ipairs(battle_battler_fields) do
        table.insert(message_fields, field)
    end
    local message = JSON.json_object(message_fields)
    return message
end

local function legacy_pc_storage_page_message(frame, domain)
    local started_at = os.clock()
    local pointer_offset = address_to_domain_offset(PLATINUM_PARTY_POINTER_ADDRESS)
    local runtime_base_address = read_u32_le(domain, pointer_offset)
    local save_data_body_address = runtime_base_address
    local save_data_struct_address = runtime_base_address
        and (save_data_body_address - PLATINUM_SAVE_DATA_STRUCT_TO_BODY_OFFSET)
        or nil
    local page_info_address = nil
    local page_id = nil
    local page_location = nil
    local page_size = nil
    local page_block_id = nil
    local pc_boxes_address = nil
    local records_address = nil
    local records_offset = nil
    local raw_records = nil
    local pc_boxes_header_hex = nil
    local final_address_valid = false
    local diagnostic_candidates = {}
    local runtime_pointer_probe = {}
	local pc_discovery_summary = nil
    local pc_discovery_candidates = {}
    local pc_discovery_anchor = nil
    local pc_discovery_error = nil
    local scan_error = "Runtime base pointer is unavailable or outside Main RAM."
    local record_objects = {}

    if main_ram_address_is_valid(save_data_body_address, PLATINUM_SAVE_DATA_BODY_SIZE) then
        page_info_address = save_data_body_address + PLATINUM_SAVE_DATA_BODY_PAGE_INFO_OFFSET
            + (PC_BOXES_PAGE_ID * SAVE_PAGE_INFO_SIZE)
        local page_info_offset = address_to_domain_offset(page_info_address)
        page_id = read_u32_le(domain, page_info_offset)
        page_size = read_u32_le(domain, page_info_offset + 4)
        page_location = read_u32_le(domain, page_info_offset + 8)
        page_block_id = read_u16_le(domain, page_info_offset + 14)

        if page_id == PC_BOXES_PAGE_ID
            and page_size == PC_BOXES_PAGE_SIZE
            and page_block_id == PC_BOXES_PAGE_BLOCK_ID
            and page_location ~= nil
            and page_location > 0
            and page_location + page_size <= PLATINUM_SAVE_DATA_BODY_SIZE then
            pc_boxes_address = save_data_body_address + page_location
            records_address = pc_boxes_address + PC_BOX_RECORDS_OFFSET
            records_offset = address_to_domain_offset(records_address)
            final_address_valid = main_ram_address_is_valid(records_address, PC_BOX_RECORDS_BYTES)
            if final_address_valid then
                pc_boxes_header_hex = read_bytes_hex_at_address(
                    domain, pc_boxes_address, PC_BOXES_HEADER_BYTES
                )
                raw_records = read_bytes_bulk(domain, records_offset, PC_BOX_RECORDS_BYTES)
                scan_error = raw_records == nil and "Bulk Main RAM read for PC boxes is unavailable." or nil
            else
                scan_error = "Validated PC box record range is outside Main RAM."
            end
        else
            scan_error = string.format(
                "SaveData PC page validation failed (page=%s expected=%s size=%s block=%s location=%s).",
                tostring(page_id), tostring(PC_BOXES_PAGE_ID), tostring(page_size),
                tostring(page_block_id), tostring(page_location)
            )
        end
    elseif save_data_body_address ~= nil then
        scan_error = "Runtime base pointer is outside Main RAM."
    end

    if raw_records == nil and main_ram_address_is_valid(save_data_body_address, PLATINUM_SAVE_DATA_BODY_SIZE) then
        for delta = -0x40, 0x40, 4 do
            local candidate_page_info_address = save_data_body_address
                + PLATINUM_SAVE_DATA_BODY_PAGE_INFO_OFFSET
                + (PC_BOXES_PAGE_ID * SAVE_PAGE_INFO_SIZE)
                + delta
            local candidate_offset = address_to_domain_offset(candidate_page_info_address)
            local candidate_page_id = read_u32_le(domain, candidate_offset)
            local candidate_page_size = read_u32_le(domain, candidate_offset + 4)
            local candidate_location = read_u32_le(domain, candidate_offset + 8)
            local candidate_block_id = read_u16_le(domain, candidate_offset + 14)
            local candidate_ok = candidate_page_id == PC_BOXES_PAGE_ID
                and candidate_page_size == PC_BOXES_PAGE_SIZE
                and candidate_block_id == PC_BOXES_PAGE_BLOCK_ID
                and candidate_location ~= nil
                and candidate_location > 0
                and candidate_location + candidate_page_size <= PLATINUM_SAVE_DATA_BODY_SIZE
            if candidate_ok then
                local candidate_pc_boxes_address = save_data_body_address + candidate_location
                local candidate_records_address = candidate_pc_boxes_address + PC_BOX_RECORDS_OFFSET
                local candidate_records_offset = address_to_domain_offset(candidate_records_address)
                local candidate_records = nil
                local candidate_non_empty = 0
                local candidate_checksum_valid = 0
                if main_ram_address_is_valid(candidate_records_address, PC_BOX_RECORDS_BYTES) then
                    candidate_records = read_bytes_bulk(
                        domain, candidate_records_offset, PC_BOX_RECORDS_BYTES
                    )
                    candidate_non_empty, candidate_checksum_valid = score_pc_records(candidate_records)
                end
                diagnostic_candidates[#diagnostic_candidates + 1] = JSON.json_object({
                    {"page_info_address", string.format("0x%08X", candidate_page_info_address)},
                    {"delta_from_expected", delta},
                    {"page_id", candidate_page_id},
                    {"page_size", candidate_page_size},
                    {"page_location", string.format("0x%08X", candidate_location)},
                    {"page_block_id", candidate_block_id},
                    {"pc_boxes_address", string.format("0x%08X", candidate_pc_boxes_address)},
                    {"records_address", string.format("0x%08X", candidate_records_address)},
                    {"non_empty_records", candidate_non_empty},
                    {"checksum_valid_records", candidate_checksum_valid},
                })
            end
        end
    end

    if raw_records ~= nil then
        for record_index = 0, (PC_BOXES_COUNT * PC_BOX_SLOTS) - 1 do
            local record_start = (record_index * PC_BOX_RECORD_SIZE) + 1
            local empty_header = true
            for byte_index = 0, 7 do
                local value = type(raw_records) == "string"
                    and string.byte(raw_records, record_start + byte_index)
                    or raw_records[record_start + byte_index]
                if value == nil or value ~= 0 then
                    empty_header = false
                    break
                end
            end
            if not empty_header then
                local box_index = math.floor(record_index / PC_BOX_SLOTS) + 1
                local slot_index = (record_index % PC_BOX_SLOTS) + 1
                local record_address = records_address + (record_index * PC_BOX_RECORD_SIZE)
                local raw_hex = bytes_to_hex_range(raw_records, record_start, PC_BOX_RECORD_SIZE)
                if raw_hex ~= nil then
                    record_objects[#record_objects + 1] = JSON.json_object({
                        {"box", box_index},
                        {"slot", slot_index},
                        {"address", string.format("0x%08X", record_address)},
                        {"raw_hex", raw_hex},
                    })
                end
            end
        end
    end

    local scan_ok = raw_records ~= nil
    local message = JSON.json_object({
        {"type", "pc_storage"},
        {"lua_build_id", LUA_BUILD_ID},
        {"lua_game_id", LUA_GAME_ID},
        {"lua_game_version", LUA_GAME_VERSION},
        {"lua_script_source", LUA_SCRIPT_SOURCE},
        {"pc_storage_reader_version", 3},
        {"run_id", SessionState.run_id},
        {"frame", frame},
        {"domain", domain},
        {"scan_ok", scan_ok},
        {"scan_error", scan_error},
        {"pc_storage_discovery_requested", false},
        {"pc_storage_discovery_result", false},
        {"pc_storage_anchor_command_received", false},
        {"pc_storage_anchor_result", false},
        {"pc_storage_anchor_result_ok", false},
        {"pc_storage_anchor_address", nil},
        {"pc_storage_anchor_scan_started_frame", nil},
        {"pc_storage_anchor_scan_completed_frame", nil},
        {"pc_storage_anchor_error", nil},
        {"save_data_pointer_slot", string.format("0x%08X", PLATINUM_PARTY_POINTER_ADDRESS)},
        {"save_data_pointer_slot_offset", pointer_offset and string.format("0x%08X", pointer_offset) or nil},
        {"save_data_pointer_semantics", "runtime base pointer used by party reader; SaveData ownership unproven"},
        {"save_data_pointer", save_data_body_address and string.format("0x%08X", save_data_body_address) or nil},
        {"runtime_base_address", runtime_base_address and string.format("0x%08X", runtime_base_address) or nil},
        {"save_data_body_address", save_data_body_address and string.format("0x%08X", save_data_body_address) or nil},
        {"save_data_struct_address_inferred", save_data_struct_address and string.format("0x%08X", save_data_struct_address) or nil},
        {"save_data_body_to_page_info_offset", string.format("0x%X", PLATINUM_SAVE_DATA_BODY_PAGE_INFO_OFFSET)},
        {"save_data_struct_to_body_offset", string.format("0x%X", PLATINUM_SAVE_DATA_STRUCT_TO_BODY_OFFSET)},
        {"page_info_address", page_info_address and string.format("0x%08X", page_info_address) or nil},
        {"page_id", page_id},
        {"page_id_expected", PC_BOXES_PAGE_ID},
        {"page_location", page_location and string.format("0x%08X", page_location) or nil},
        {"page_size", page_size and string.format("0x%X", page_size) or nil},
        {"page_block_id", page_block_id},
        {"pc_boxes_address", pc_boxes_address and string.format("0x%08X", pc_boxes_address) or nil},
        {"pc_boxes_offset_from_body", page_location and string.format("0x%X", page_location) or nil},
        {"pc_boxes_header_hex", pc_boxes_header_hex},
        {"final_address_valid", final_address_valid},
        {"records_address", records_address and string.format("0x%08X", records_address) or nil},
        {"records_offset", records_offset and string.format("0x%08X", records_offset) or nil},
        {"box_count", PC_BOXES_COUNT},
        {"slots_per_box", PC_BOX_SLOTS},
        {"record_stride", PC_BOX_RECORD_SIZE},
        {"records_scanned", PC_BOXES_COUNT * PC_BOX_SLOTS},
        {"bytes_read", raw_records ~= nil and PC_BOX_RECORDS_BYTES or 0},
        {"scan_duration_ms", math.floor((os.clock() - started_at) * 1000 + 0.5)},
        {"runtime_base_probe_window", nil},
        {"runtime_base_probe_rows", "__RUNTIME_BASE_PROBE_ROWS__"},
		{"pc_storage_discovery_summary", JSON.json_raw(pc_discovery_summary or "null")},
		{"pc_storage_discovery_candidates", "__PC_DISCOVERY_CANDIDATES__"},
		{"pc_storage_anchor_diagnostic", JSON.json_raw(pc_discovery_anchor or "null")},
		{"diagnostic_candidates", "__PC_DIAGNOSTIC_CANDIDATES__"},
        {"records", "__PC_RECORDS__"},
    })
    message = message:gsub(
        '"__RUNTIME_BASE_PROBE_ROWS__"',
        "[" .. table.concat(runtime_pointer_probe, ",") .. "]"
    )
	message = message:gsub(
		'"__PC_DISCOVERY_CANDIDATES__"',
		"[" .. table.concat(pc_discovery_candidates, ",") .. "]"
	)
	message = message:gsub(
		'"__PC_DIAGNOSTIC_CANDIDATES__"',
		"[" .. table.concat(diagnostic_candidates, ",") .. "]"
	)
    return message:gsub('"__PC_RECORDS__"', "[" .. table.concat(record_objects, ",") .. "]")
end

local function pc_storage_message(frame, domain)
    local started_at = os.clock()
    local first_record_address = PCState.session_pc_first_record_address
    local run_matches = PCState.session_pc_run_id == SessionState.run_id
    local address_valid = run_matches
        and main_ram_address_is_valid(first_record_address, PC_BOX_RECORDS_BYTES)
    local records = {}
    local raw_records = nil
    local scan_error = nil
    local malformed_records = 0
    local occupied_records = 0
    local empty_records = 0
    local rediscovery_required = false
    local runtime_base_address = read_u32_le(
        domain,
        address_to_domain_offset(PLATINUM_PARTY_POINTER_ADDRESS)
    )

    if not run_matches and first_record_address ~= nil then
        clear_session_pc_resolver("Lua run ID changed")
        first_record_address = nil
        address_valid = false
    end
    if not address_valid then
        scan_error = "PC layout unresolved for this Lua session; run Discover Current PC Layout."
    elseif domain == nil then
        scan_error = "Nintendo DS Main RAM domain is unavailable."
        address_valid = false
    else
        raw_records = read_bytes_bulk(
            domain,
            address_to_domain_offset(first_record_address),
            PC_BOX_RECORDS_BYTES
        )
        if raw_records == nil then
            scan_error = "Could not read the cached 540-slot PC region."
            address_valid = false
        else
            for record_index = 0, (PC_BOXES_COUNT * PC_BOX_SLOTS) - 1 do
                local record_start = (record_index * PC_BOX_RECORD_SIZE) + 1
                local empty_header = true
                for byte_index = 0, 7 do
                    local value = bulk_byte(raw_records, record_start + byte_index)
                    if value == nil or value ~= 0 then
                        empty_header = false
                        break
                    end
                end
                if empty_header then
                    empty_records = empty_records + 1
                elseif bulk_u16_le(raw_records, record_start + 4) ~= 0
                    or not boxed_record_checksum_valid(raw_records, record_start) then
                    malformed_records = malformed_records + 1
                else
                    occupied_records = occupied_records + 1
                    local raw_hex = bytes_to_hex_range(
                        raw_records, record_start, PC_BOX_RECORD_SIZE
                    )
                    if raw_hex ~= nil then
                        records[#records + 1] = JSON.json_object({
                            {"box", math.floor(record_index / PC_BOX_SLOTS) + 1},
                            {"slot", (record_index % PC_BOX_SLOTS) + 1},
                            {"address", string.format(
                                "0x%08X", first_record_address + record_index * PC_BOX_RECORD_SIZE
                            )},
                            {"raw_hex", raw_hex},
                        })
                    end
                end
            end
            if malformed_records > 0 then
                PCState.session_pc_invalid_scans = PCState.session_pc_invalid_scans + 1
                scan_error = string.format(
                    "Cached PC layout stale: %d malformed slots (%d consecutive scans).",
                    malformed_records, PCState.session_pc_invalid_scans
                )
                records = {}
                if PCState.session_pc_invalid_scans >= 3 then
                    rediscovery_required = true
                    clear_session_pc_resolver(scan_error)
                end
            else
                PCState.session_pc_invalid_scans = 0
            end
        end
    end

    local records_address = address_valid and first_record_address or nil
    local header_hex = nil
    if address_valid and first_record_address >= MAIN_RAM_BASE + 0x28 then
        header_hex = read_bytes_hex_at_address(domain, first_record_address - 0x28, 0x28)
    end
    local payload = JSON.json_object({
        {"type", "pc_storage"},
        {"lua_build_id", LUA_BUILD_ID},
        {"lua_game_id", LUA_GAME_ID},
        {"lua_game_version", LUA_GAME_VERSION},
        {"lua_script_source", LUA_SCRIPT_SOURCE},
        {"pc_storage_reader_version", 4},
        {"run_id", SessionState.run_id},
        {"lua_run_id", SessionState.run_id},
        {"rom_session_identity", SessionState.session_rom_identity},
        {"frame", frame},
        {"domain", domain},
        {"scan_ok", raw_records ~= nil and address_valid and malformed_records == 0},
        {"scan_error", scan_error},
        {"pc_storage_resolver_kind", "session_pc_cache"},
        {"pc_storage_resolver_status", malformed_records > 0 and "stale"
            or (address_valid and "resolved for this session" or "unresolved")},
        {"session_pc_first_record_address", first_record_address
            and string.format("0x%08X", first_record_address) or nil},
        {"session_pc_run_id", PCState.session_pc_run_id},
        {"pc_storage_acquisition_enabled", raw_records ~= nil and address_valid
            and malformed_records == 0},
        {"save_data_pointer_slot", string.format("0x%08X", PLATINUM_PARTY_POINTER_ADDRESS)},
        {"save_data_pointer", runtime_base_address
            and string.format("0x%08X", runtime_base_address) or nil},
        {"runtime_base_address", runtime_base_address
            and string.format("0x%08X", runtime_base_address) or nil},
        {"save_data_pointer_semantics", "party-reader pointer; not used to resolve PC storage"},
        {"page_info_address", nil},
        {"page_id", nil},
        {"page_block_id", nil},
        {"page_location", nil},
        {"page_size", nil},
        {"pc_boxes_address", records_address
            and string.format("0x%08X", records_address) or nil},
        {"records_address", records_address
            and string.format("0x%08X", records_address) or nil},
        {"records_offset", records_address
            and string.format("0x%08X", address_to_domain_offset(records_address)) or nil},
        {"pc_boxes_header_hex", header_hex},
        {"final_address_valid", address_valid},
        {"box_count", PC_BOXES_COUNT},
        {"slots_per_box", PC_BOX_SLOTS},
        {"record_stride", PC_BOX_RECORD_SIZE},
        {"records_scanned", PC_BOXES_COUNT * PC_BOX_SLOTS},
        {"occupied_records", occupied_records},
        {"empty_records", empty_records},
        {"malformed_records", malformed_records},
        {"pc_storage_rediscovery_required", rediscovery_required},
        {"bytes_read", raw_records ~= nil and PC_BOX_RECORDS_BYTES or 0},
        {"scan_duration_ms", math.floor((os.clock() - started_at) * 1000 + 0.5)},
        {"records", "__SESSION_PC_RECORDS__"},
    })
    local encoded = payload:gsub(
        '"__SESSION_PC_RECORDS__"', "[" .. table.concat(records, ",") .. "]"
    )
    return encoded, raw_records ~= nil and address_valid and malformed_records == 0,
        occupied_records, empty_records, malformed_records, scan_error
end

function PCState.validate_cache_install(cache_run_id, cache_address, frame, domain)
    local run_match = cache_run_id == SessionState.run_id
    local range_valid = main_ram_address_is_valid(cache_address, PC_BOX_RECORDS_BYTES)
    local domain_offset = range_valid and address_to_domain_offset(cache_address) or nil
    local runtime_base, party_address, party_count, party_valid = read_active_party_range(domain)
    local party_end = party_valid and party_address + party_count * PARTY_POKEMON_SIZE or nil
    local party_overlap = party_valid and range_valid and not (
        cache_address + PC_BOX_RECORDS_BYTES <= party_address or cache_address >= party_end
    ) or false
    local first_record = range_valid and domain ~= nil and read_bytes_bulk(
        domain, domain_offset, PC_BOX_RECORD_SIZE
    ) or nil
    local anchor_bytes_read = first_record and #first_record or 0
    local anchor_read = anchor_bytes_read == PC_BOX_RECORD_SIZE
    local anchor_valid, species, stored_checksum, calculated_checksum, shuffle_index =
        false, nil, nil, nil, nil
    if anchor_read then
        anchor_valid, species, stored_checksum, calculated_checksum, shuffle_index =
            boxed_record_checksum_valid(first_record, 1)
    end
    local checksum_pass = stored_checksum ~= nil
        and calculated_checksum ~= nil and stored_checksum == calculated_checksum
    local species_pass = anchor_valid
        and species == PCState.session_pc_expected_species_id
    local sanity_value = anchor_read and bulk_u16_le(first_record, 5) or nil
    local sanity_pass = sanity_value == 0
    local expected_nickname_hex = PCState.pc_storage_discovery_expected_identity
        and PCState.pc_storage_discovery_expected_identity.nickname_hex or nil
    local preflight_pass = run_match and range_valid and party_valid and not party_overlap
        and anchor_read and checksum_pass and species_pass and sanity_pass
    local validation_payload, layout_pass, occupied, empty, malformed, layout_error =
        nil, false, 0, 0, 0, nil
    if preflight_pass then
        PCState.session_pc_first_record_address = cache_address
        PCState.session_pc_run_id = SessionState.run_id
        PCState.session_pc_invalid_scans = 0
        validation_payload, layout_pass, occupied, empty, malformed, layout_error =
            pc_storage_message(frame, domain)
        layout_pass = layout_pass and occupied + empty == PC_BOXES_COUNT * PC_BOX_SLOTS
        if not layout_pass then
            clear_session_pc_resolver(layout_error or "Cached PC slot count invalid")
        end
    end
    local error = nil
    if not run_match then
        error = "Lua run ID mismatch."
    elseif not range_valid then
        error = "PC region is outside NDS Main RAM."
    elseif not party_valid then
        error = "Active party RAM range could not be read safely."
    elseif party_overlap then
        error = "PC region overlaps active party RAM."
    elseif not anchor_read then
        error = "Could not read all 136 anchor bytes from Main RAM."
    elseif not checksum_pass then
        error = "Anchor BoxPokemon checksum failed."
    elseif not species_pass then
        error = "Anchor species differs from expected Box 1 Slot 1 species."
    elseif not sanity_pass then
        error = "Anchor BoxPokemon sanity field is nonzero."
    elseif not layout_pass then
        error = layout_error or "Cached 540-slot PC layout validation failed."
    end
    local check = function(fields)
        return JSON.json_raw(JSON.json_object(fields))
    end
    local diagnostic = JSON.json_object({
        {"run_id_match", check({
            {"expected", cache_run_id}, {"actual", SessionState.run_id}, {"pass", run_match},
        })},
        {"main_ram_range", check({
            {"first_record", cache_address and string.format("0x%08X", cache_address) or nil},
            {"final_record_end", cache_address and string.format("0x%08X", cache_address + PC_BOX_RECORDS_BYTES) or nil},
            {"main_ram_start", string.format("0x%08X", MAIN_RAM_BASE)},
            {"main_ram_end", string.format("0x%08X", MAIN_RAM_BASE + MAIN_RAM_SIZE)},
            {"domain_offset", domain_offset and string.format("0x%08X", domain_offset) or nil},
            {"pass", range_valid},
        })},
        {"party_range", check({
            {"runtime_base", runtime_base and string.format("0x%08X", runtime_base) or nil},
            {"available", party_valid},
            {"party_base", party_address and string.format("0x%08X", party_address) or nil},
            {"party_count", party_count},
            {"party_record_size", PARTY_POKEMON_SIZE},
            {"party_byte_count", party_valid and party_count * PARTY_POKEMON_SIZE or nil},
            {"party_end", party_end and string.format("0x%08X", party_end) or nil},
            {"pc_region_overlaps_party", party_valid and party_overlap or nil},
            {"pass", party_valid and not party_overlap},
        })},
        {"anchor_record_ram_read", check({
            {"absolute_address", cache_address and string.format("0x%08X", cache_address) or nil},
            {"domain_offset", domain_offset and string.format("0x%08X", domain_offset) or nil},
            {"bytes_read", anchor_bytes_read},
            {"first_64_bytes_hex", anchor_read and bytes_to_hex_range(first_record, 1, 64) or nil},
            {"raw_136_bytes_hex", anchor_read and bytes_to_hex_range(first_record, 1, 136) or nil},
            {"pass", anchor_read},
        })},
        {"anchor_checksum", check({
            {"stored", stored_checksum}, {"calculated", calculated_checksum},
            {"pass", checksum_pass}, {"shuffle_index", shuffle_index},
        })},
        {"anchor_species", check({
            {"decoded_species_id", species},
            {"expected_species_id", PCState.session_pc_expected_species_id},
            {"pass", species_pass},
        })},
        {"anchor_nickname", check({
            {"decoded_nickname", nil}, {"expected_nickname_hex", expected_nickname_hex},
            {"checked_by_lua", false}, {"pass", nil},
        })},
        {"anchor_sanity", check({{"value", sanity_value}, {"pass", sanity_pass}})},
        {"layout", check({
            {"valid_occupied", occupied}, {"empty", empty}, {"malformed", malformed},
            {"attempted", preflight_pass}, {"pass", layout_pass},
        })},
    })
    return layout_pass and preflight_pass, validation_payload, occupied, empty,
        malformed, error, diagnostic
end

local function close_client()
    if client ~= nil then
        pcall(function() client:close() end)
    end
    client = nil
    TransportState.command_buffer = ""
    TransportState.queued_tcp_lines = {}
    if CoordinateState.coordinate_scan ~= nil and CoordinateState.coordinate_scan.transport == "tcp" then
        CoordinateState.coordinate_scan = nil
    end
end

local function send_scan_error(capture_id, message, transport)
    CoordinateState.coordinate_scan = {
        capture_id = capture_id,
        stage = "error",
        message = message,
        transport = transport,
        file_started = false,
    }
end

local function process_command(line, frame, domain, transport)
    line = line:gsub("\r", "")
	local acknowledged_scan_id = line:match("^PC_ACK_DISCOVERY_SCAN|([%w%-]+)$")
	if acknowledged_scan_id ~= nil then
		if PCState.pc_discovery_scan == nil
			and PCState.pc_discovery_retry_scan ~= nil
			and PCState.pc_discovery_retry_scan.scan_id == acknowledged_scan_id then
			PCState.pc_discovery_retry_scan = nil
			PCState.pc_discovery_resend_queue = {}
			PCState.pc_discovery_resend_queued = {}
			print(string.format(
				"EV Tracker PC discovery cache acknowledged: scan=%s frame=%d transport=%s",
				acknowledged_scan_id,
				frame,
				tostring(transport)
			))
		else
			print(string.format(
				"EV Tracker PC discovery cache acknowledgment ignored: scan=%s active_scan=%s",
				acknowledged_scan_id,
				PCState.pc_discovery_scan and PCState.pc_discovery_scan.scan_id or "none"
			))
		end
		return
	end
	local acknowledged_search_id = line:match("^PC_ACK_POKEMON_SEARCH|([%w%-]+)$")
	if acknowledged_search_id ~= nil then
		if PCState.pc_pokemon_search == nil
			and PCState.pc_pokemon_search_waiting_ack == acknowledged_search_id then
			PCState.pc_pokemon_search_waiting_ack = nil
			print(string.format(
				"EV Tracker Pokemon search result acknowledged: scan=%s frame=%d transport=%s",
				acknowledged_search_id,
				frame,
				tostring(transport)
			))
		else
			print(string.format(
				"EV Tracker Pokemon search acknowledgment ignored: scan=%s active_scan=%s",
				acknowledged_search_id,
				PCState.pc_pokemon_search and PCState.pc_pokemon_search.scan_id or "none"
			))
		end
		return
	end
	local retry_scan_id, retry_index_text = line:match(
		"^PC_RETRY_DISCOVERY_CHUNK|([%w%-]+)|(%d+)$"
	)
	if retry_scan_id ~= nil then
		local retry_index = tonumber(retry_index_text)
		local retry_scan = PCState.pc_discovery_retry_scan
		local cached_line = nil
		if retry_scan ~= nil and retry_scan.scan_id == retry_scan_id then
			cached_line = retry_scan.chunk_cache[retry_index]
		end
		print(string.format(
			"EV Tracker PC discovery resend received: scan=%s chunk=%d cache=%s frame=%d transport=%s",
			retry_scan_id,
			retry_index,
			cached_line ~= nil and "hit" or "miss",
			frame,
			tostring(transport)
		))
		if cached_line ~= nil then
			local resend_key = retry_scan_id .. ":" .. tostring(retry_index)
			if PCState.pc_discovery_resend_queued[resend_key] then
				print(string.format(
					"EV Tracker PC discovery resend already queued: scan=%s chunk=%d",
					retry_scan_id,
					retry_index
				))
			elseif #PCState.pc_discovery_resend_queue >= PC_DISCOVERY_RESEND_QUEUE_MAX then
				print(string.format(
					"EV Tracker PC discovery resend queue full: scan=%s chunk=%d",
					retry_scan_id,
					retry_index
				))
			else
				table.insert(PCState.pc_discovery_resend_queue, {
					scan_id = retry_scan_id,
					chunk_index = retry_index,
					line = cached_line,
					key = resend_key,
				})
				PCState.pc_discovery_resend_queued[resend_key] = true
				print(string.format(
					"EV Tracker PC discovery resend queued: scan=%s chunk=%d " ..
					"queue_length=%d scan_active=%s",
					retry_scan_id,
					retry_index,
					#PCState.pc_discovery_resend_queue,
					tostring(PCState.pc_discovery_scan ~= nil)
				))
			end
		else
			print(string.format(
				"EV Tracker PC discovery resend not found: scan=%s chunk=%d",
				retry_scan_id,
				retry_index
			))
			if retry_scan ~= nil and retry_scan.scan_id == retry_scan_id then
				pc_discovery_fail(
					retry_scan_id,
					retry_scan.mode,
					retry_scan.anchor_address,
					"Requested discovery chunk is no longer in the retransmit cache.",
					frame
				)
			else
				print("EV Tracker PC discovery ignored stale resend command.")
			end
		end
		return
	end
	if line == "PC_CANCEL_DISCOVERY" then
		print(string.format(
			"EV Tracker PC discovery cancel command received: frame=%d transport=%s",
			frame,
			tostring(transport)
		))
		PCState.pc_storage_discovery_cancel_requested = true
		return
	end
	local pointer_header_hex, pointer_record_hex = line:match(
		"^PC_SEARCH_PC_POINTERS|(%x+)|(%x+)$"
	)
	if pointer_header_hex ~= nil then
		local header_address = tonumber(pointer_header_hex, 16)
		local first_record_address = tonumber(pointer_record_hex, 16)
		print(string.format(
			"EV Tracker structure pointer search command received: header=0x%08X " ..
			"first_record=0x%08X frame=%d transport=%s",
			header_address,
			first_record_address,
			frame,
			tostring(transport)
		))
		if main_ram_address_is_valid(header_address, 0x28)
			and main_ram_address_is_valid(first_record_address, PC_BOX_RECORD_SIZE)
			and first_record_address - header_address == 0x28 then
			PCState.pc_structure_pointer_search_requested = {
				header = header_address,
				first_record = first_record_address,
			}
		else
			print("EV Tracker structure pointer search command rejected: invalid target addresses.")
		end
		return
	end
	local search_pid_hex, search_checksum_hex, search_species_hex, search_anchor_hex =
		line:match("^PC_SEARCH_POKEMON|(%x+)|(%x+)|(%x+)|(%x+)$")
	if search_pid_hex ~= nil then
		local pid = tonumber(search_pid_hex, 16)
		local checksum = tonumber(search_checksum_hex, 16)
		local species_id = tonumber(search_species_hex, 16)
		local anchor_address = tonumber(search_anchor_hex, 16)
		print(string.format(
			"EV Tracker Pokemon identity search command received: pid=0x%08X checksum=0x%04X species=%d anchor=0x%08X frame=%d transport=%s",
			pid,
			checksum,
			species_id,
			anchor_address,
			frame,
			tostring(transport)
		))
		if pid ~= nil and checksum ~= nil and species_id ~= nil
			and species_id >= 1 and species_id <= 493
			and main_ram_address_is_valid(anchor_address, PC_BOX_RECORD_SIZE) then
			PCState.pc_pokemon_search_requested = {
				pid = pid,
				checksum = checksum,
				species_id = species_id,
				anchor_address = anchor_address,
			}
		else
			print("EV Tracker Pokemon identity search command rejected: invalid identity or anchor.")
		end
		return
	end
	if line == "PC_SCAN_NOW" then
		PCState.pc_storage_scan_requested = true
		return
	end
	if line == "PC_TEST_SAVE_PC_OFFSETS" then
		if PCState.pc_save_offset_test ~= nil or PCState.pc_save_offset_test_requested then
			print("EV Tracker save-offset diagnostic request rejected: already active.")
		else
			PCState.pc_save_offset_test_requested = true
			print(string.format(
				"EV Tracker save-offset diagnostic command received: frame=%d transport=%s",
				frame,
				tostring(transport)
			))
		end
		return
	end
	if line == "PC_SEARCH_SRAM_POKEMON" then
		if PCState.pc_sram_search ~= nil or PCState.pc_sram_search_requested then
			print("EV Tracker live SRAM search request rejected: already active.")
		else
			PCState.pc_sram_search_requested = true
			print(string.format(
				"EV Tracker live SRAM search command received: frame=%d transport=%s",
				frame,
				tostring(transport)
			))
		end
		return
	end
	local cache_run_id, cache_address_hex = line:match(
		"^PC_CACHE_SESSION_PC|([%w%-]+)|(%x+)$"
	)
	if cache_run_id ~= nil then
		print(string.format(
			"EV Tracker Lua received PC_CACHE_SESSION_PC: requested_run=%s current_run=%s address=%s transport=%s",
			cache_run_id, SessionState.run_id, cache_address_hex, tostring(transport)
		))
		local cache_address = tonumber(cache_address_hex, 16)
		local cache_valid, validation_payload, occupied, empty, malformed,
			validation_error, validation_diagnostic = PCState.validate_cache_install(
				cache_run_id, cache_address, frame, domain
			)
		print(string.format(
			"EV Tracker session PC cache command: run_id=%s current_run=%s " ..
			"address=0x%08X accepted=%s transport=%s",
			cache_run_id,
			SessionState.run_id,
			cache_address or 0,
			tostring(cache_valid),
			tostring(transport)
		))
		print(string.format(
			"EV Tracker Lua PC cache validation: address=0x%08X valid=%d empty=%d invalid=%d pass=%s error=%s",
			cache_address or 0, occupied, empty, malformed,
			tostring(cache_valid), tostring(validation_error)
		))
		local confirmation_sent = emit_pc_discovery_event({
			{"type", cache_valid and "pc_storage_cache_ready" or "pc_storage_cache_rejected"},
			{"lua_run_id", SessionState.run_id},
			{"expected_lua_run_id", cache_run_id},
			{"session_pc_first_record_address", cache_address
				and string.format("0x%08X", cache_address) or nil},
			{"accepted", cache_valid},
			{"occupied_records", occupied},
			{"empty_records", empty},
			{"malformed_records", malformed},
			{"error", cache_valid and nil or validation_error},
			{"cache_validation", JSON.json_raw(validation_diagnostic)},
		}, frame)
		print(string.format(
			"EV Tracker Lua PC cache confirmation emitted: sent=%s accepted=%s run=%s address=0x%08X",
			tostring(confirmation_sent), tostring(cache_valid), SessionState.run_id,
			cache_address or 0
		))
		if cache_valid and validation_payload ~= nil then
			local scan_sent = send_line(validation_payload, frame)
			print(string.format(
				"EV Tracker Lua first cached PC scan emitted: sent=%s occupied=%d empty=%d invalid=%d",
				tostring(scan_sent), occupied, empty, malformed
			))
		end
		return
	end
	local expected_species, expected_nickname_hex, requested_run_id = line:match(
		"^PC_DISCOVER_CURRENT_LAYOUT|(%d+)|([%x]*)|([%w%-]+)$"
	)
	if expected_species == nil then
		expected_species, expected_nickname_hex = line:match(
			"^PC_DISCOVER_CURRENT_LAYOUT|(%d+)|([%x]*)$"
		)
	end
	if expected_species ~= nil then
		if requested_run_id ~= nil and requested_run_id ~= SessionState.run_id then
			print(string.format(
				"EV Tracker rejected discovery from old Lua run: requested=%s current=%s",
				requested_run_id, SessionState.run_id
			))
			return
		end
		expected_species = tonumber(expected_species)
		if expected_species ~= nil and expected_species >= 1 and expected_species <= 493
			and #expected_nickname_hex <= 20 then
			clear_session_pc_resolver("PC layout rediscovery requested")
			PCState.session_pc_expected_species_id = expected_species
			PCState.pc_storage_discovery_anchor_address = nil
			PCState.pc_storage_discovery_expected_identity = {
				species_id = expected_species,
				nickname_hex = string.upper(expected_nickname_hex),
			}
			PCState.pc_storage_discovery_requested = true
			print(string.format(
				"EV Tracker session PC layout discovery received: species=%d nickname_hex=%s " ..
				"frame=%d transport=%s",
				expected_species,
				string.upper(expected_nickname_hex),
				frame,
				tostring(transport)
			))
		else
			print("EV Tracker session PC layout command rejected: invalid expected identity.")
		end
		return
	end
	local anchor_hex = line:match("^PC_DISCOVER_STORAGE|BOX1_SLOT1|(%x+)$")
	if anchor_hex ~= nil then
		local anchor_address = tonumber(anchor_hex, 16)
		print(string.format(
			"EV Tracker PC anchor command received: address=0x%08X frame=%d transport=%s",
			anchor_address,
			frame,
			tostring(transport)
		))
		if main_ram_address_is_valid(anchor_address, PC_BOX_RECORD_SIZE) then
			PCState.pc_storage_discovery_anchor_address = anchor_address
			PCState.pc_storage_discovery_requested = true
		else
			print(string.format(
				"EV Tracker PC anchor command rejected: address outside Main RAM: 0x%08X",
				anchor_address
			))
		end
		return
	end
	local inspect_hex = line:match("^PC_INSPECT_POKEMON|(%x+)$")
	if inspect_hex ~= nil then
		local inspect_address = tonumber(inspect_hex, 16)
		print(string.format(
			"EV Tracker Pokemon inspection command received: address=0x%08X frame=%d transport=%s",
			inspect_address,
			frame,
			tostring(transport)
		))
		if main_ram_address_is_valid(inspect_address, PC_BOX_RECORD_SIZE) then
			PCState.pc_storage_inspection_anchor_address = inspect_address
			PCState.pc_storage_inspection_requested = true
		else
			print(string.format(
				"EV Tracker Pokemon inspection rejected: address outside Main RAM: 0x%08X",
				inspect_address
			))
		end
		return
	end
	if line:match("^PC_DISCOVER_STORAGE|BOX1_SLOT1") then
		print("EV Tracker PC anchor command rejected: expected hex address suffix")
		return
	end
	if line == "PC_DISCOVER_STORAGE" then
		PCState.pc_storage_discovery_anchor_address = nil
		PCState.pc_storage_discovery_requested = true
		return
    end
    local sequence, walk_mode = line:match("^WALK|(%d+)|START|(horizontal)$")
    if sequence == nil then
        sequence, walk_mode = line:match("^WALK|(%d+)|START|(vertical)$")
    end
    if sequence ~= nil then
        local x, y = player_coordinates(domain)
        if x == nil or y == nil then
            pause_friendship_walk("coordinates", "Paused — Coordinates unavailable", true)
        elseif battle_active_now(domain) then
            pause_friendship_walk("battle", "Paused — Battle", true)
        else
            CoordinateState.friendship_walk.enabled = true
            CoordinateState.friendship_walk.mode = walk_mode
            CoordinateState.friendship_walk.direction = walk_mode == "horizontal" and "Left" or "Up"
            CoordinateState.friendship_walk.pending_direction = nil
            CoordinateState.friendship_walk.reversal_until_frame = nil
            CoordinateState.friendship_walk.injected_direction = nil
            CoordinateState.friendship_walk.b_injected = false
            CoordinateState.friendship_walk.status = "Walking " .. CoordinateState.friendship_walk.direction
            CoordinateState.friendship_walk.pause_reason = nil
            -- This lease uses wall-clock time; movement/reversal remains frame-based.
            CoordinateState.friendship_walk.last_command_time = os.time()
            CoordinateState.friendship_walk.release_pending = false
        end
        CoordinateState.friendship_walk.ack_sequence = tonumber(sequence)
        CoordinateState.friendship_walk.ack_frame = frame
        CoordinateState.friendship_walk.ack_action = "START"
        return
    end

    sequence = line:match("^WALK|(%d+)|STOP$")
    if sequence ~= nil then
        CoordinateState.friendship_walk.enabled = false
        CoordinateState.friendship_walk.direction = nil
        CoordinateState.friendship_walk.pending_direction = nil
        CoordinateState.friendship_walk.reversal_until_frame = nil
        CoordinateState.friendship_walk.injected_direction = nil
        CoordinateState.friendship_walk.b_injected = false
        CoordinateState.friendship_walk.status = "Idle"
        CoordinateState.friendship_walk.pause_reason = nil
        CoordinateState.friendship_walk.release_pending = true
        CoordinateState.friendship_walk.ack_sequence = tonumber(sequence)
        CoordinateState.friendship_walk.ack_frame = frame
        CoordinateState.friendship_walk.ack_action = "STOP"
        return
    end

    sequence = line:match("^WALK|(%d+)|PING$")
    if sequence ~= nil then
        if CoordinateState.friendship_walk.enabled then
            CoordinateState.friendship_walk.last_command_time = os.time()
        end
        CoordinateState.friendship_walk.ack_sequence = tonumber(sequence)
        CoordinateState.friendship_walk.ack_frame = frame
        CoordinateState.friendship_walk.ack_action = "PING"
        return
    end
    local direction_sequence, walk_direction = line:match("^WALK|(%d+)|DIRECTION|(Left)$")
    if direction_sequence == nil then
        direction_sequence, walk_direction = line:match("^WALK|(%d+)|DIRECTION|(Right)$")
    end
    if direction_sequence == nil then
        direction_sequence, walk_direction = line:match("^WALK|(%d+)|DIRECTION|(Up)$")
    end
    if direction_sequence == nil then
        direction_sequence, walk_direction = line:match("^WALK|(%d+)|DIRECTION|(Down)$")
    end
    if walk_direction ~= nil and CoordinateState.friendship_walk.enabled then
        CoordinateState.friendship_walk.direction = nil
        CoordinateState.friendship_walk.pending_direction = walk_direction
        CoordinateState.friendship_walk.reversal_until_frame = frame + WALK_REVERSAL_GRACE_FRAMES
        CoordinateState.friendship_walk.injected_direction = nil
        CoordinateState.friendship_walk.status = "Blocked — reversing"
        CoordinateState.friendship_walk.last_command_time = os.time()
        CoordinateState.friendship_walk.ack_sequence = tonumber(direction_sequence)
        CoordinateState.friendship_walk.ack_frame = frame
        CoordinateState.friendship_walk.ack_action = "DIRECTION"
        return
    elseif direction_sequence ~= nil then
        CoordinateState.friendship_walk.ack_sequence = tonumber(direction_sequence)
        CoordinateState.friendship_walk.ack_frame = frame
        CoordinateState.friendship_walk.ack_action = "DIRECTION"
        return
    end
    if line == "CLEAR_PREVIEW" then
        CoordinateState.coordinate_preview = nil
        CoordinateState.previous_preview_x = nil
        CoordinateState.previous_preview_y = nil
        return
    end
    local x_offset, y_offset, data_type = line:match("^PREVIEW|(%d+)|(%d+)|(u16)$")
    if x_offset == nil then
        x_offset, y_offset, data_type = line:match("^PREVIEW|(%d+)|(%d+)|(s16)$")
    end
    if x_offset ~= nil then
        CoordinateState.coordinate_preview = {
            x_offset = tonumber(x_offset),
            y_offset = tonumber(y_offset),
            data_type = data_type,
        }
        CoordinateState.previous_preview_x = nil
        CoordinateState.previous_preview_y = nil
        return
    end

    local capture_id, label, start_offset, length = line:match(
        "^CAPTURE|([%w%-]+)|([%w]+)|(%d+)|(%d+)$"
    )
    if capture_id == nil then
        return
    end
    if CoordinateState.coordinate_scan ~= nil then
        send_scan_error(
            capture_id,
            "Another coordinate capture is already in progress.",
            transport
        )
        return
    end
    start_offset = tonumber(start_offset)
    length = tonumber(length)
    local total = domain_size(domain)
    if domain == nil or total == nil or start_offset + length > total
        or length < 2 or length > MAX_COORDINATE_SCAN_BYTES
        or start_offset % 2 ~= 0 or length % 2 ~= 0 then
        send_scan_error(
            capture_id,
            "Invalid Main RAM range or unavailable memory domain.",
            transport
        )
        return
    end
    CoordinateState.coordinate_scan = {
        capture_id = capture_id,
        label = label,
        start_offset = start_offset,
        length = length,
        cursor = 0,
        domain = domain,
        stage = "start",
        transport = transport,
        file_started = false,
    }
end

local function service_pc_discovery_resend(frame)
    if PCState.pc_discovery_scan ~= nil or #PCState.pc_discovery_resend_queue == 0 then
        return
    end
    local resend = table.remove(PCState.pc_discovery_resend_queue, 1)
    PCState.pc_discovery_resend_queued[resend.key] = nil
    local emitted = send_line(resend.line, frame)
    print(string.format(
        "EV Tracker PC discovery resend emitted: scan=%s chunk=%d success=%s " ..
        "queue_length=%d",
        resend.scan_id,
        resend.chunk_index,
        tostring(emitted),
        #PCState.pc_discovery_resend_queue
    ))
    if not emitted then
        table.insert(PCState.pc_discovery_resend_queue, 1, resend)
        PCState.pc_discovery_resend_queued[resend.key] = true
    end
end

local function poll_command_file(frame, domain)
    local poll_interval = PCState.pc_discovery_scan ~= nil and 1 or COMMAND_FILE_POLL_INTERVAL
    if COMMAND_FILE == nil
        or frame - TransportState.last_command_file_poll_frame < poll_interval then
        return
    end
    TransportState.last_command_file_poll_frame = frame
    local handle = io.open(COMMAND_FILE, "r")
    if handle == nil then
        return
    end
    local line = handle:read("*l")
    handle:close()
    os.remove(COMMAND_FILE)
    if line ~= nil and line ~= "" then
        process_command(line, frame, domain, "file")
    end
end

local function poll_commands(frame, domain)
    if client ~= nil then
        for _ = 1, 4 do
            local ok, line, receive_error, partial = pcall(function()
                return client:receive("*l")
            end)
            if not ok then
                close_client()
                break
            end
            if line ~= nil then
                local complete = TransportState.command_buffer .. line
                TransportState.command_buffer = ""
                process_command(complete, frame, domain, "tcp")
            elseif partial ~= nil and partial ~= "" then
                TransportState.command_buffer = TransportState.command_buffer .. partial
                if #TransportState.command_buffer > 256 then
                    TransportState.command_buffer = ""
                end
                break
            elseif receive_error == "closed" then
                close_client()
                break
            else
                break
            end
        end
    end
    poll_command_file(frame, domain)
end

local function send_coordinate_event(scan, line, frame)
    if scan.transport == "tcp" then
        return send_line(line, frame)
    end
    if COORDINATE_SCAN_FILE == nil then
        write_fallback(line)
        return false
    end
    local mode = scan.file_started and "a" or "w"
    local handle = io.open(COORDINATE_SCAN_FILE, mode)
    if handle == nil then
        write_fallback(line)
        return false
    end
    handle:write(line)
    handle:write("\n")
    handle:close()
    scan.file_started = true
    return true
end

local function advance_coordinate_scan(frame)
    local scan = CoordinateState.coordinate_scan
    if scan == nil then
        return
    end
    if scan.transport == "tcp" and client == nil then
        CoordinateState.coordinate_scan = nil
        return
    end
    if scan.transport == "tcp" and (TransportState.pending_tcp_line ~= nil or #TransportState.queued_tcp_lines > 0) then
        return
    end

    if scan.stage == "error" then
        send_coordinate_event(scan, JSON.json_object({
            {"type", "coordinate_scan_error"},
            {"capture_id", scan.capture_id},
            {"message", scan.message},
        }), frame)
        CoordinateState.coordinate_scan = nil
        return
    end
    if scan.stage == "start" then
        send_coordinate_event(scan, JSON.json_object({
            {"type", "coordinate_scan_start"},
            {"capture_id", scan.capture_id},
            {"label", scan.label},
            {"start_offset", scan.start_offset},
            {"length", scan.length},
        }), frame)
        scan.stage = "chunks"
        return
    end
    if scan.cursor < scan.length then
        local chunk_length = math.min(COORDINATE_SCAN_CHUNK_BYTES, scan.length - scan.cursor)
        local chunk = read_bytes(scan.domain, scan.start_offset + scan.cursor, chunk_length)
        local encoded = bytes_to_hex(chunk, chunk_length)
        if encoded == nil then
            send_scan_error(
                scan.capture_id,
                "BizHawk could not read this RAM range.",
                scan.transport
            )
            return
        end
        send_coordinate_event(scan, JSON.json_object({
            {"type", "coordinate_scan_chunk"},
            {"capture_id", scan.capture_id},
            {"chunk_offset", scan.cursor},
            {"data_hex", encoded},
        }), frame)
        scan.cursor = scan.cursor + chunk_length
        return
    end
    send_coordinate_event(scan, JSON.json_object({
        {"type", "coordinate_scan_end"},
        {"capture_id", scan.capture_id},
    }), frame)
    CoordinateState.coordinate_scan = nil
end

local function ensure_client(frame)
    if socket == nil then
        return false
    end
    if client ~= nil then
        return true
    end
    if frame - TransportState.last_connect_attempt < CONNECT_RETRY_INTERVAL then
        return false
    end

    TransportState.last_connect_attempt = frame
    local ok, tcp = pcall(function() return socket.tcp() end)
    if not ok or tcp == nil then
        return false
    end
    tcp:settimeout(CONNECT_TIMEOUT_SECONDS)
    local connected = tcp:connect(HOST, PORT)
    if connected == 1 or connected == true then
        tcp:settimeout(0)
        client = tcp
        print("EV Tracker: connected to Python TCP server at " .. HOST .. ":" .. PORT)
        return true
    end
    pcall(function() tcp:close() end)
    return false
end

write_fallback = function(line)
    if FALLBACK_FILE == nil then
        return false
    end
    local handle = io.open(FALLBACK_FILE, "a")
    if handle ~= nil then
        local size = handle:seek("end") or 0
        local discovery_active = PCState.pc_sram_search ~= nil
            or PCState.pc_sram_search_requested
            or PCState.pc_discovery_scan ~= nil
            or PCState.pc_discovery_retry_scan ~= nil
            or PCState.pc_pokemon_search ~= nil
            or PCState.pc_pokemon_search_requested ~= nil
            or PCState.pc_pokemon_search_waiting_ack ~= nil
            or #PCState.pc_discovery_resend_queue > 0
        if size >= MAX_FALLBACK_BYTES and not discovery_active then
            handle:close()
            local reset_line = JSON.json_object({
                {"type", "transport_file_reset"},
                {"old_size", size},
                {"new_size", 0},
                {"reason", "fallback file reached configured size limit"},
                {"initiator", "Lua fallback writer"},
                {"discovery_scan_active", false},
                {"active_scan_id", nil},
                {"frame", get_frame_count()},
            })
            print(string.format(
                "EV Tracker fallback transport reset: old_size=%d new_size=0 " ..
                "reason=size-limit initiator=Lua discovery_active=false scan_id=none",
                size
            ))
            handle = io.open(FALLBACK_FILE, "w")
            if handle ~= nil then
                handle:write(reset_line .. "\n")
            end
        elseif size >= MAX_FALLBACK_BYTES and not TransportState.fallback_limit_warned then
            TransportState.fallback_limit_warned = true
            print(string.format(
                "EV Tracker fallback size limit deferred: size=%d limit=%d " ..
                "discovery_active=%s scan_id=%s",
                size,
                MAX_FALLBACK_BYTES,
                tostring(discovery_active),
                PCState.pc_discovery_scan and PCState.pc_discovery_scan.scan_id
                    or (PCState.pc_discovery_retry_scan and PCState.pc_discovery_retry_scan.scan_id)
                or (PCState.pc_pokemon_search and PCState.pc_pokemon_search.scan_id)
                    or (PCState.pc_sram_search and PCState.pc_sram_search.scan_id)
                    or (PCState.pc_sram_search_requested and "live-sram-search-pending")
                    or PCState.pc_pokemon_search_waiting_ack
                    or "none"
            ))
        end
    end
    if handle ~= nil then
        local ok, result = pcall(function()
            return handle:write(line .. "\n")
        end)
        pcall(function() handle:close() end)
        return ok and result ~= nil
    end
    return false
end

local function flush_tcp()
    if client == nil then
        return
    end
    if TransportState.pending_tcp_line == nil and #TransportState.queued_tcp_lines > 0 then
        TransportState.pending_tcp_line = table.remove(TransportState.queued_tcp_lines, 1)
        TransportState.pending_tcp_offset = 1
    end
    if TransportState.pending_tcp_line == nil then
        return
    end

    local ok, sent, send_error, last_sent = pcall(function()
        return client:send(TransportState.pending_tcp_line, TransportState.pending_tcp_offset)
    end)
    if not ok then
        send_error = sent
        sent = nil
        last_sent = nil
    end
    if sent ~= nil then
        TransportState.pending_tcp_line = nil
        TransportState.pending_tcp_offset = 1
    elseif send_error == "timeout" then
        TransportState.pending_tcp_offset = (last_sent or (TransportState.pending_tcp_offset - 1)) + 1
        if TransportState.pending_tcp_offset > #TransportState.pending_tcp_line then
            TransportState.pending_tcp_line = nil
            TransportState.pending_tcp_offset = 1
        end
    else
        pcall(function() client:close() end)
        client = nil
        if TransportState.pending_tcp_line ~= nil then
            write_fallback(TransportState.pending_tcp_line:sub(1, -2))
        end
        for _, queued_line in ipairs(TransportState.queued_tcp_lines) do
            write_fallback(queued_line:sub(1, -2))
        end
        TransportState.pending_tcp_line = nil
        TransportState.pending_tcp_offset = 1
        TransportState.queued_tcp_lines = {}
    end
end

send_line = function(line, frame)
    if ensure_client(frame) and client ~= nil then
        local data = line .. "\n"
        if TransportState.pending_tcp_line == nil then
            TransportState.pending_tcp_line = data
            TransportState.pending_tcp_offset = 1
        else
            TransportState.queued_tcp_lines[#TransportState.queued_tcp_lines + 1] = data
        end
        flush_tcp()
        return true
    end
    return write_fallback(line)
end

local domains = domain_names()
local active_domain = choose_main_ram_domain(domains)
print("EV Tracker RAM reader started; domain=" .. tostring(active_domain))

local next_domain_refresh = get_frame_count() + DOMAIN_REFRESH_INTERVAL
local last_domain_refresh_frame = get_frame_count()
local domain_refresh_ticks = 0
local last_heartbeat_frame = nil
local last_party_frame = nil
local last_pc_storage_frame = nil

while true do
    local frame = get_frame_count()
    flush_tcp()
    local current_rom_identity = get_rom_session_identity()
    if SessionState.session_rom_identity ~= nil and current_rom_identity ~= SessionState.session_rom_identity then
        if PCState.pc_sram_search ~= nil or PCState.pc_sram_search_requested then
            SRAMDiag.fail_pc_sram_search(
                PCState.pc_sram_search and PCState.pc_sram_search.scan_id or nil,
                "ROM/session identity changed during the live SRAM scan.",
                frame
            )
            PCState.pc_sram_search_requested = false
        end
        if PCState.pc_save_offset_test ~= nil then
            SaveRAMDiag.fail_pc_save_offset_test(
                PCState.pc_save_offset_test.test_id,
                "ROM/session identity changed during save-offset capture.",
                frame
            )
        end
        if PCState.pc_storage_discovery_requested or PCState.pc_storage_inspection_requested
            or PCState.pc_discovery_scan ~= nil or PCState.pc_pokemon_search ~= nil
            or PCState.pc_pokemon_search_requested ~= nil
            or PCState.pc_structure_pointer_search ~= nil
            or PCState.pc_structure_pointer_search_requested ~= nil then
            cancel_pc_discovery(frame)
        end
        clear_session_pc_resolver("ROM/session identity changed")
        SessionState.run_id = new_lua_run_id()
        SessionState.session_rom_identity = current_rom_identity
    end
    local connected_now = client ~= nil
    if connected_now and SessionState.session_had_connection and not SessionState.session_was_connected then
        if PCState.pc_sram_search ~= nil or PCState.pc_sram_search_requested then
            SRAMDiag.fail_pc_sram_search(
                PCState.pc_sram_search and PCState.pc_sram_search.scan_id or nil,
                "BizHawk transport reconnected during the live SRAM scan.",
                frame
            )
            PCState.pc_sram_search_requested = false
        end
        if PCState.pc_save_offset_test ~= nil then
            SaveRAMDiag.fail_pc_save_offset_test(
                PCState.pc_save_offset_test.test_id,
                "BizHawk transport reconnected during save-offset capture.",
                frame
            )
        end
        print("EV Tracker transport reconnected; preserving PC cache for the current Lua run")
    end
    if connected_now then
        SessionState.session_had_connection = true
    end
    SessionState.session_was_connected = connected_now
    domain_refresh_ticks = domain_refresh_ticks + 1
    if frame < last_domain_refresh_frame
        or frame >= next_domain_refresh
        or (active_domain == nil and domain_refresh_ticks >= HEARTBEAT_INTERVAL) then
        domains = domain_names()
        active_domain = choose_main_ram_domain(domains)
        last_domain_refresh_frame = frame
        next_domain_refresh = frame + (active_domain and DOMAIN_REFRESH_INTERVAL or HEARTBEAT_INTERVAL)
        domain_refresh_ticks = 0
    end

    poll_commands(frame, active_domain)
    apply_friendship_walk(frame, active_domain)
    if CoordinateState.coordinate_scan ~= nil then
        if frame ~= last_party_frame and frame % PARTY_INTERVAL == 0 then
            last_party_frame = frame
            send_line(party_memory_message(frame, active_domain), frame)
        end
        if frame ~= last_heartbeat_frame and frame % HEARTBEAT_INTERVAL == 0
            and TransportState.pending_tcp_line == nil and #TransportState.queued_tcp_lines == 0 then
            last_heartbeat_frame = frame
            local reads = diagnostic_reads(active_domain)
            local message = JSON.json_object({
                {"type", "heartbeat"},
                {"lua_build_id", LUA_BUILD_ID},
                {"lua_game_id", LUA_GAME_ID},
                {"lua_game_version", LUA_GAME_VERSION},
                {"lua_script_source", LUA_SCRIPT_SOURCE},
                {"run_id", SessionState.run_id},
                {"frame", frame},
                {"core", get_core_name()},
                {"domains", domains},
                {"active_domain", active_domain},
                {"diagnostic_reads", "__READS__"},
                {"transport", socket and "tcp_or_file" or "file"},
                {"connected", client ~= nil},
            })
            message = message:gsub("\"__READS__\"", "[" .. table.concat(reads, ",") .. "]")
            send_line(message, frame)
        else
            advance_coordinate_scan(frame)
        end
    else
        if frame ~= last_heartbeat_frame and frame % HEARTBEAT_INTERVAL == 0 then
            last_heartbeat_frame = frame
            local reads = diagnostic_reads(active_domain)
            local message = JSON.json_object({
                {"type", "heartbeat"},
                {"lua_build_id", LUA_BUILD_ID},
                {"lua_game_id", LUA_GAME_ID},
                {"lua_game_version", LUA_GAME_VERSION},
                {"lua_script_source", LUA_SCRIPT_SOURCE},
                {"run_id", SessionState.run_id},
                {"frame", frame},
                {"core", get_core_name()},
                {"domains", domains},
                {"active_domain", active_domain},
                {"diagnostic_reads", "__READS__"},
                {"transport", socket and "tcp_or_file" or "file"},
                {"connected", client ~= nil},
            })
            message = message:gsub("\"__READS__\"", "[" .. table.concat(reads, ",") .. "]")
            send_line(message, frame)
        end
        if frame ~= last_party_frame and frame % PARTY_INTERVAL == 0 then
            last_party_frame = frame
            local message = party_memory_message(frame, active_domain)
            send_line(message, frame)
        end
    end
	if PCState.pc_storage_discovery_cancel_requested then
		PCState.pc_storage_discovery_cancel_requested = false
		cancel_pc_discovery(frame)
	elseif PCState.pc_save_offset_test_requested then
		PCState.pc_save_offset_test_requested = false
		SaveRAMDiag.start_pc_save_offset_test(frame)
	elseif PCState.pc_sram_search_requested then
		PCState.pc_sram_search_requested = false
		SRAMDiag.start_pc_sram_search(frame)
	elseif PCState.pc_pokemon_search_requested ~= nil then
		local identity = PCState.pc_pokemon_search_requested
		PCState.pc_pokemon_search_requested = nil
		start_pc_pokemon_search(frame, active_domain, identity)
	elseif PCState.pc_structure_pointer_search_requested ~= nil then
		local targets = PCState.pc_structure_pointer_search_requested
		PCState.pc_structure_pointer_search_requested = nil
		start_pc_structure_pointer_search(frame, active_domain, targets)
	elseif PCState.pc_storage_inspection_requested then
        local inspect_address = PCState.pc_storage_inspection_anchor_address
        PCState.pc_storage_inspection_requested = false
        PCState.pc_storage_inspection_anchor_address = nil
        start_pc_discovery(frame, active_domain, nil, inspect_address)
    elseif PCState.pc_storage_discovery_requested then
        local anchor_address = PCState.pc_storage_discovery_anchor_address
        local expected_identity = PCState.pc_storage_discovery_expected_identity
        PCState.pc_storage_discovery_requested = false
        PCState.pc_storage_discovery_anchor_address = nil
        PCState.pc_storage_discovery_expected_identity = nil
        start_pc_discovery(frame, active_domain, anchor_address, nil, expected_identity)
    end
    if PCState.pc_storage_scan_requested
        or (frame ~= last_pc_storage_frame and frame % PC_STORAGE_INTERVAL == 0) then
        last_pc_storage_frame = frame
		PCState.pc_storage_scan_requested = false
		local payload = pc_storage_message(frame, active_domain)
		send_line(payload, frame)
    end
    SaveRAMDiag.advance_pc_save_offset_test(frame)
    SRAMDiag.advance_pc_sram_search(frame)
    advance_pc_discovery(frame, active_domain)
    advance_pc_pokemon_search(frame, active_domain)
    advance_pc_structure_pointer_search(frame, active_domain)
    service_pc_discovery_resend(frame)
    emu.frameadvance()
end
