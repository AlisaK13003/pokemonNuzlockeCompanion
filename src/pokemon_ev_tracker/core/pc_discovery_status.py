"""Actionable messages for PC discovery failures, shared by UI and logging."""

from collections.abc import Mapping


def pc_discovery_failure_message(summary: Mapping, *, session_layout: bool = False) -> str:
    error = str(summary.get("resolver_error") or summary.get("error") or "").lower()
    if "cache" in error or "confirm" in error:
        return (
            "PC layout was found, but the emulator did not confirm its cache. "
            "Check Box 1, Slot 1 matches the selected Pokemon. "
            "Reload the BizHawk Lua script, then retry PC discovery. Party tracking still works."
        )
    if session_layout:
        if summary.get("matching_box1_slot1_records_found") == 0:
            return (
                "PC monitoring paused: the saved Box 1, Slot 1 Pokemon was not found. "
                "If your PC is empty, party tracking still works. Otherwise select the Pokemon "
                "currently in Box 1, Slot 1 and retry PC discovery. To stop startup scans, "
                "turn off 'Rediscover PC layout automatically' in Settings."
            )
        return (
            "PC monitoring paused: the box layout could not be verified. "
            "Check the selected Pokemon matches Box 1, Slot 1, then retry PC discovery. "
            "Party tracking still works."
        )
    return (
        "PC discovery could not complete. Check the emulator connection and retry. "
        "If it keeps failing, reload the BizHawk Lua script. See advanced diagnostics for details."
    )
