from __future__ import annotations

import streamlit as st


def apply_dark_mode(*, animate_background: bool = True) -> None:
    """
    Force a consistent dark theme across the whole app.
    Streamlit's theme config covers most widgets; this CSS patch handles the rest
    (sidebar, separators, some widget surfaces, and general typography contrast).
    """
    css = (
        """
<style>
/* ===== Night mode (global) ===== */
:root {
  --nm-bg: #0b0f14;
  --nm-panel: #111827;
  --nm-surface: #1f2937;
  --nm-surface-2: #2a2f3a;
  --nm-border: rgba(255, 255, 255, 0.12);
  --nm-text: #e5e7eb;
  --nm-text-2: #9ca3af;
  --nm-text-3: rgba(229, 231, 235, 0.72);
  --nm-grid: rgba(85, 184, 255, 0.11);
  --nm-grid-glow: rgba(85, 184, 255, 0.24);
  --nm-background-glow: rgba(85, 184, 255, 0.18);
}

html, body {
  background: var(--nm-bg) !important;
  color: var(--nm-text) !important;
}

/* App background */
.stApp {
  isolation: isolate;
  background:
    radial-gradient(circle at 50% 42%, var(--nm-background-glow), transparent 34rem),
    var(--nm-bg) !important;
  color: var(--nm-text) !important;
}

/*
 * The grid is based on the Essay Podcast background: a 44px fine grid over a
 * 220px glow grid. The first layer keeps the lines visible; the second carries
 * a wide breathing highlight from left to right so the grid itself never moves.
 */
.stApp::before,
.stApp::after {
  content: "";
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  background-image:
    linear-gradient(var(--nm-grid) 1px, transparent 1px),
    linear-gradient(90deg, var(--nm-grid) 1px, transparent 1px),
    linear-gradient(var(--nm-grid-glow) 1px, transparent 1px),
    linear-gradient(90deg, var(--nm-grid-glow) 1px, transparent 1px);
  background-size: 44px 44px, 44px 44px, 220px 220px, 220px 220px;
}

.stApp::before {
  opacity: 0.48;
  -webkit-mask-image: radial-gradient(
    ellipse at center,
    #000 18%,
    rgba(0, 0, 0, 0.82) 52%,
    transparent 92%
  );
  mask-image: radial-gradient(
    ellipse at center,
    #000 18%,
    rgba(0, 0, 0, 0.82) 52%,
    transparent 92%
  );
  animation: dnd-grid-base-color 20s ease-in-out infinite;
}

.stApp::after {
  opacity: 0.36;
  -webkit-mask-image: radial-gradient(
    ellipse at center,
    #000 0%,
    rgba(0, 0, 0, 0.94) 24%,
    rgba(0, 0, 0, 0.54) 54%,
    transparent 78%
  );
  mask-image: radial-gradient(
    ellipse at center,
    #000 0%,
    rgba(0, 0, 0, 0.94) 24%,
    rgba(0, 0, 0, 0.54) 54%,
    transparent 78%
  );
  -webkit-mask-repeat: no-repeat;
  mask-repeat: no-repeat;
  -webkit-mask-size: 48% 118%;
  mask-size: 48% 118%;
  -webkit-mask-position: -92% center;
  mask-position: -92% center;
  animation:
    dnd-grid-wave 14s linear infinite,
    dnd-grid-glow-color 20s ease-in-out infinite;
  will-change: opacity, filter, mask-position;
}

@keyframes dnd-grid-wave {
  0% {
    opacity: 0.34;
    -webkit-mask-position: -92% center;
    mask-position: -92% center;
  }
  18% { opacity: 0.64; }
  50% { opacity: 0.96; }
  82% { opacity: 0.64; }
  100% {
    opacity: 0.34;
    -webkit-mask-position: 192% center;
    mask-position: 192% center;
  }
}

@keyframes dnd-grid-base-color {
  0%, 100% { filter: hue-rotate(0deg); }
  50% { filter: hue-rotate(52deg); }
}

@keyframes dnd-grid-glow-color {
  0%, 100% {
    filter: hue-rotate(0deg) drop-shadow(0 0 2px rgba(85, 184, 255, 0.34));
  }
  50% {
    filter: hue-rotate(52deg) drop-shadow(0 0 8px rgba(168, 85, 247, 0.48));
  }
}

/* ===== Full-screen app shell =====
 * Keep Streamlit's sidebar opener functional, but remove the native header's
 * background and layout footprint. The opener becomes a small floating control
 * instead of forcing an empty bar across the viewport.
 */
header[data-testid="stHeader"] {
  height: 0 !important;
  min-height: 0 !important;
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  overflow: visible !important;
  pointer-events: none !important;
}

header[data-testid="stHeader"] [data-testid="stToolbar"] {
  min-height: 0 !important;
  padding: 0 !important;
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  overflow: visible !important;
  pointer-events: none !important;
}

[data-testid="stDecoration"] {
  display: none !important;
}

header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"] {
  position: fixed !important;
  top: max(8px, env(safe-area-inset-top)) !important;
  left: max(8px, env(safe-area-inset-left)) !important;
  z-index: 1000000 !important;
  margin: 0 !important;
  pointer-events: auto !important;
  background: rgba(17, 24, 39, 0.78) !important;
  border: 1px solid var(--nm-border) !important;
  border-radius: 10px !important;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.32) !important;
  backdrop-filter: blur(10px) !important;
  -webkit-backdrop-filter: blur(10px) !important;
}

header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"] button {
  background: rgba(17, 24, 39, 0.78) !important;
  border: 1px solid var(--nm-border) !important;
  border-radius: 10px !important;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.32) !important;
  backdrop-filter: blur(10px) !important;
  -webkit-backdrop-filter: blur(10px) !important;
}

/* Sidebar */
[data-testid="stSidebar"] > div {
  background: var(--nm-panel) !important;
}
[data-testid="stSidebar"] * {
  color: var(--nm-text) !important;
}

/* Share the first row with the native collapse control. */
[data-testid="stSidebarContent"]:has(.st-key-sidebar_back) {
  position: relative;
}
[data-testid="stSidebarContent"]:has(.st-key-sidebar_back) [data-testid="stSidebarHeader"] {
  position: absolute;
  top: 1rem;
  right: 1.25rem;
  width: 2.375rem;
  height: 2.375rem;
  min-height: 0;
  padding: 0;
  margin: 0;
  z-index: 1;
}
[data-testid="stSidebarContent"]:has(.st-key-sidebar_back) [data-testid="stLogoSpacer"] {
  display: none;
}
[data-testid="stSidebarContent"]:has(.st-key-sidebar_back) [data-testid="stSidebarCollapseButton"] {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  visibility: visible;
  opacity: 1;
}
[data-testid="stSidebarContent"]:has(.st-key-sidebar_back) [data-testid="stSidebarUserContent"] {
  padding-top: 1rem;
}
[data-testid="stSidebar"] .st-key-sidebar_back {
  width: calc(100% - 3rem);
}
[data-testid="stSidebar"] .st-key-sidebar_back button {
  min-height: 2.375rem;
}

/* Headers / captions */
.stMarkdown, .stCaption, .st-emotion-cache-1c7y2kd, .st-emotion-cache-16idsys {
  color: var(--nm-text) !important;
}
.stCaption, [data-testid="stCaptionContainer"] {
  color: var(--nm-text-2) !important;
}

/* Dividers */
[data-testid="stDivider"] hr, hr {
  border-color: var(--nm-border) !important;
}

/* Inputs (text/textarea/select) */
.stTextInput input,
.stTextArea textarea,
.stSelectbox div[data-baseweb="select"] > div,
.stMultiSelect div[data-baseweb="select"] > div {
  background: var(--nm-surface) !important;
  color: var(--nm-text) !important;
  border-color: var(--nm-border) !important;
}

/* Placeholders */
.stTextInput input::placeholder,
.stTextArea textarea::placeholder {
  color: var(--nm-text-3) !important;
}

/* Labels */
label, .stTextInput label, .stTextArea label, .stSelectbox label, .stRadio label {
  color: var(--nm-text) !important;
}

/* Radio/checkbox text */
[data-testid="stRadio"] * , [data-testid="stCheckbox"] * {
  color: var(--nm-text) !important;
}

/* Glass changes only the button material. Page-owned geometry, colors,
 * typography, focus indicators and interaction behavior stay unchanged. */
:where(.stApp button, [data-testid="stDialog"] button) {
  background: linear-gradient(145deg, rgba(255,255,255,.065), transparent), rgba(31,41,55,.28) !important;
  -webkit-backdrop-filter: blur(16px) saturate(130%) !important;
  backdrop-filter: blur(16px) saturate(130%) !important;
}
:where(.stApp button:is([kind="primary"], [kind="primaryFormSubmit"]), [data-testid="stDialog"] button:is([kind="primary"], [kind="primaryFormSubmit"])) {
  background: linear-gradient(145deg, rgba(255,255,255,.08), transparent), rgba(255,75,75,.26) !important;
}

/* Buttons */
.stButton > button {
  border-color: var(--nm-border) !important;
}
.stButton > button:not([kind="primary"]) {
  background: linear-gradient(145deg, rgba(255,255,255,.065), transparent), rgba(31,41,55,.28) !important;
  color: var(--nm-text) !important;
}

/* Info/warn/error/success blocks */
[data-testid="stAlert"] {
  background: rgba(31, 41, 55, 0.85) !important;
  border: 1px solid var(--nm-border) !important;
  color: var(--nm-text) !important;
}

/* Tables/dataframes surfaces */
[data-testid="stDataFrame"], [data-testid="stTable"] {
  background: var(--nm-surface) !important;
  border: 1px solid var(--nm-border) !important;
}

/* Make code blocks readable */
pre, code {
  background: rgba(31, 41, 55, 0.65) !important;
  color: var(--nm-text) !important;
  border-color: var(--nm-border) !important;
}

/* ===== Audio player (pure black to blend with chat background) ===== */
[data-testid="stAudio"] {
  background: #000 !important;
  border-radius: 8px !important;
  overflow: hidden !important;
}
[data-testid="stAudio"] audio {
  background: #000 !important;
  width: 100% !important;
}
audio::-webkit-media-controls-enclosure {
  background: #000 !important;
  border-radius: 0 !important;
}
audio::-webkit-media-controls-panel {
  background: #000 !important;
}

@media (prefers-reduced-motion: reduce) {
  .stApp::before {
    animation: none;
    filter: hue-rotate(26deg);
  }
  .stApp::after {
    animation: none;
    opacity: 0.30;
    filter: hue-rotate(26deg);
    -webkit-mask-position: center;
    mask-position: center;
  }
}

</style>
        """
    )

    if not animate_background:
        css = css.replace(
            "</style>",
            """
/* Keep the background appearance on content-heavy pages without continuously
 * repainting full-viewport masks and filters. */
.stApp::before {
  animation: none;
  filter: hue-rotate(26deg);
}
.stApp::after {
  animation: none;
  opacity: 0.30;
  filter: hue-rotate(26deg);
  will-change: auto;
  -webkit-mask-position: center;
  mask-position: center;
}
</style>
            """,
        )

    st.html(css)
