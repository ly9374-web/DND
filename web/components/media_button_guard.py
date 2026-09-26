"""Immediate browser-side click protection; generation locks also live in the queue."""
from pathlib import Path
import uuid

import streamlit as st


def render_media_button_guard() -> None:
    script = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")
    # A fresh acknowledgment is emitted only after the media panel finishes rendering.
    st.html(f'<span data-media-render-ack="{uuid.uuid4().hex}" style="display:none"></span>')
    st.iframe(script, height=1)
