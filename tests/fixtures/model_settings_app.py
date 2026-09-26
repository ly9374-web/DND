"""Isolated API-key settings UI; never reads the user's real saved credentials."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("VS_IMAGE_NOVEL_DATA_DIR"), "Use an isolated data directory"

import streamlit as st

from web.pages import model_settings


st.set_page_config(layout="wide")
model_settings.render()
