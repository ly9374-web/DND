"""Bounded memory versions: current and previous, attached to chat turn IDs."""
from copy import deepcopy


def checkpoint(state: dict, turns: list) -> dict:
    anchor = str(state.get("last_update_turn_id") or "")
    if not anchor and state.get("short") and turns:
        # Legacy memory has no known creation boundary; never pretend it predates the chat.
        anchor = str(turns[-1].id)
    return {"after_turn_id": anchor, "state": deepcopy(state)}


def record_update(history: list, before: dict, after: dict, turns: list, anchor: str) -> list:
    versions = deepcopy(history[-2:])
    if not versions:
        versions = [checkpoint(before, turns)]
    versions.append({"after_turn_id": str(anchor or ""), "state": deepcopy(after)})
    return versions[-2:]


def rewind(state: dict, history: list, original_turns: list, remaining_turns: list) -> tuple[dict, list, str]:
    """Plan a rewind without mutating live memory (also used for staged resends)."""
    versions = deepcopy(history[-2:]) or [checkpoint(state, original_turns)]
    remaining_ids = {str(turn.id) for turn in remaining_turns}
    latest_anchor = str(versions[-1].get("after_turn_id") or "")
    if not latest_anchor or latest_anchor in remaining_ids:
        return deepcopy(state), versions, ""
    eligible = [item for item in versions if not item.get("after_turn_id") or item["after_turn_id"] in remaining_ids]
    if eligible:
        return deepcopy(eligible[-1]["state"]), eligible[-2:], "已恢复上一版 Story Brain 快照。"
    # Keep the invalid boundary so repeated undo cannot mislabel stale memory as restored.
    return deepcopy(state), versions, "已超出记忆快照保留范围（或旧记录没有快照）；问答可撤回，但当前 Story Brain 无法回退，仍保留现有内容。"
