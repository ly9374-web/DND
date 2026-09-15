"""Register prompt widgets and render the browser/server draft bridge."""

from __future__ import annotations

from uuid import uuid4

import streamlit as st

from app.services import prompt_drafts
from web.components.prompt_draft_guard import prompt_draft_guard_component


_FIELDS_KEY = "_prompt_draft_guard_fields"


def begin() -> None:
    st.session_state[_FIELDS_KEY] = []
    st.session_state["_prompt_draft_navigation"] = []


def scope() -> str:
    user_id = str(st.session_state.get("user_id", "") or "anonymous")
    mode = str(st.session_state.get("auth_mode", "") or "user")
    space = "hidden" if bool(st.session_state.get("hidden_unlocked", False)) else "ordinary"
    return f"{mode}:{user_id}:{space}"


def protect(
    widget_key: str,
    baseline: str,
    label: str,
    *,
    requires_commit: bool = True,
    strip_on_save: bool = True,
) -> None:
    fields = st.session_state.setdefault(_FIELDS_KEY, [])
    item = {
        "widget_key": str(widget_key),
        "baseline": str(baseline or ""),
        "label": str(label or widget_key),
        "requires_commit": bool(requires_commit),
        "strip_on_save": bool(strip_on_save),
    }
    fields[:] = [existing for existing in fields if existing.get("widget_key") != widget_key]
    fields.append(item)


def navigation(widget_key: str) -> None:
    """Register only controls that leave the current page/Prompt section."""
    st.session_state.setdefault("_prompt_draft_navigation", []).append(widget_key)


def save_action(widget_key: str, *field_keys: str) -> None:
    """Associate fields with their actual save button, never its visible text."""
    for field in st.session_state.get(_FIELDS_KEY, []):
        if field["widget_key"] in field_keys:
            field["save_key"] = widget_key


def saved(*field_keys: str) -> None:
    """Acknowledge only after the editor's persistence operation succeeds."""
    current_scope = scope()
    acknowledgements = st.session_state.setdefault("_prompt_draft_saved", {})
    for key in field_keys:
        acknowledgements[f"{current_scope}:{key}"] = {
            "widget_key": key,
            "value": str(st.session_state.get(key, "") or ""),
            "revision": uuid4().hex,
        }
    prompt_drafts.apply_browser_snapshot(current_scope, [], list(field_keys))


def page_id() -> str:
    page = str(st.session_state.get("nav_page", ""))
    if page == "settings":
        return f"{page}:{st.session_state.get('ordinary_prompt_section', '最终设定')}"
    return page


def render() -> None:
    fields = list(st.session_state.get(_FIELDS_KEY) or [])
    current_scope = scope()
    current_page = page_id()
    prefix = f"{current_scope}:"
    event = prompt_draft_guard_component(
        scope=current_scope,
        page=current_page,
        fields=fields,
        navigation_keys=st.session_state.get("_prompt_draft_navigation", []),
        server_drafts=prompt_drafts.scoped_entries(current_scope),
        saved_fields=[
            value for key, value in st.session_state.get("_prompt_draft_saved", {}).items()
            if key.startswith(prefix)
        ],
        receipt=st.session_state.get("_prompt_draft_guard_receipt", ""),
        key="dnd_prompt_draft_guard_bridge",
    )
    if not isinstance(event, dict) or event.get("event") != "snapshot":
        return
    # A component can return its previous value during a page/account change.
    if event.get("scope") != current_scope or event.get("page") != current_page:
        return
    event_revision = str(event.get("revision") or "")
    receipt = f"{current_scope}:{event_revision}"
    if not event_revision or st.session_state.get("_prompt_draft_guard_receipt") == receipt:
        return
    prompt_drafts.apply_browser_snapshot(
        current_scope,
        event.get("drafts") if isinstance(event.get("drafts"), list) else [],
        event.get("cleared") if isinstance(event.get("cleared"), list) else [],
    )
    st.session_state["_prompt_draft_guard_receipt"] = receipt
    st.rerun()
