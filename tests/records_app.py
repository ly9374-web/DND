"""Isolated browser fixture; never reads or writes the user's saved records."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from web.nav import goto, nav_init, nav_state
from web.pages.records import render


class MemoryStore:
    def load_index_sorted(self, **kwargs):
        return st.session_state.test_records

    def rename_record(self, record_id, title, **kwargs):
        for item in st.session_state.test_records:
            if item.id == record_id:
                item.title = title

    def delete_record(self, record_id, **kwargs):
        st.session_state.test_records = [
            item for item in st.session_state.test_records if item.id != record_id
        ]


st.set_page_config(layout="wide")
nav_init("records")
st.session_state.setdefault("test_records", [
    SimpleNamespace(id="one", title="第一条记录", updated_at="2026-09-17", hidden=False),
    SimpleNamespace(id="two", title="第二条记录", updated_at="2026-09-17", hidden=False),
    SimpleNamespace(id="duplicate", title="第二条记录", updated_at="2026-09-17", hidden=False),
])
if nav_state().page == "main":
    st.write("已打开：" + nav_state().page_kwargs["record_id"])
    if st.button("返回记录"):
        goto("records")
else:
    render(store=MemoryStore())
