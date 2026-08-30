from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import streamlit as st


@dataclass
class _NavState:
    page: str
    page_kwargs: Dict[str, Any]
    history: List[tuple[str, Dict[str, Any]]]


def nav_init(default_page: str = "home"):
    if "nav_page" not in st.session_state:
        st.session_state.nav_page = default_page
    if "nav_page_kwargs" not in st.session_state:
        st.session_state.nav_page_kwargs = {}
    if "nav_history" not in st.session_state:
        st.session_state.nav_history = []


def nav_state() -> _NavState:
    nav_init()
    return _NavState(
        page=str(st.session_state.nav_page),
        page_kwargs=dict(st.session_state.nav_page_kwargs or {}),
        history=list(st.session_state.nav_history or []),
    )


def goto(page: str, push_history: bool = True, **kwargs: Any):
    nav_init()
    if push_history:
        st.session_state.nav_history.append(
            (
                str(st.session_state.nav_page),
                dict(st.session_state.nav_page_kwargs or {}),
            )
        )
    st.session_state.nav_page = page
    st.session_state.nav_page_kwargs = kwargs
    st.rerun()


def back():
    nav_init()
    history = st.session_state.nav_history
    if not history:
        st.session_state.nav_page = "home"
        st.session_state.nav_page_kwargs = {}
        st.rerun()
        return

    page, kwargs = history.pop()
    st.session_state.nav_page = page
    st.session_state.nav_page_kwargs = kwargs or {}
    st.rerun()


def get_arg(key: str, default: Optional[Any] = None) -> Any:
    nav_init()
    kwargs = st.session_state.nav_page_kwargs or {}
    return kwargs.get(key, default)

