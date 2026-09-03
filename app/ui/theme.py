from __future__ import annotations

import streamlit as st


def apply_dark_mode() -> None:
    """
    Force a consistent dark theme across the whole app.
    Streamlit's theme config covers most widgets; this CSS patch handles the rest
    (sidebar, separators, some widget surfaces, and general typography contrast).
    """
    st.html(
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

/* Buttons */
.stButton > button {
  border-color: var(--nm-border) !important;
}
.stButton > button:not([kind="primary"]) {
  background: var(--nm-surface) !important;
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

/* ===== Chat (st.chat_message) ===== */
div[data-testid="stChatMessage"] {
  align-items: flex-start !important;
  gap: 14px !important;
  padding: 16px 18px !important;
  margin: 12px 0 !important;
  border-radius: 22px !important;
  background: rgba(10, 14, 20, 0.58) !important;
  border: 1px solid rgba(255, 255, 255, 0.10) !important;
}

/* Role glows (preferred: container-level using :has) */
div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  box-shadow:
    0 0 0 1px rgba(139, 92, 246, 0.18),
    0 0 26px rgba(76, 29, 149, 0.35),
    0 12px 30px rgba(0, 0, 0, 0.55) !important;
}
div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
  box-shadow:
    0 0 0 1px rgba(56, 189, 248, 0.16),
    0 0 26px rgba(56, 189, 248, 0.22),
    0 12px 30px rgba(0, 0, 0, 0.55) !important;
}

/* Fallback role glows (content-level, works without :has) */
[data-testid="stChatMessageAvatarUser"] + [data-testid="stChatMessageContent"] {
  border-radius: 18px !important;
  box-shadow: 0 0 22px rgba(76, 29, 149, 0.22) !important;
}
[data-testid="stChatMessageAvatarAssistant"] + [data-testid="stChatMessageContent"] {
  border-radius: 18px !important;
  box-shadow: 0 0 22px rgba(56, 189, 248, 0.18) !important;
}

/* Put user avatar on the right (match reference image) */
div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  flex-direction: row-reverse !important;
}
/* Fallback ordering without :has */
[data-testid="stChatMessageAvatarUser"] {
  order: 2 !important;
}
[data-testid="stChatMessageAvatarUser"] + [data-testid="stChatMessageContent"] {
  order: 1 !important;
}

/* Avatar: use 2-color gradients (no concentric rings) */
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {
  width: 44px !important;
  height: 44px !important;
  border-radius: 999px !important;
  align-self: flex-start !important;
  margin-top: 2px !important;
  box-shadow:
    0 0 0 1px rgba(255, 255, 255, 0.10),
    0 8px 18px rgba(0, 0, 0, 0.55) !important;
}
[data-testid="stChatMessageAvatarUser"] {
  background: linear-gradient(135deg, #a855f7 0%, #0b0f14 85%) !important;
}
[data-testid="stChatMessageAvatarAssistant"] {
  background: linear-gradient(135deg, #ff4fd8 0%, #25b9ff 100%) !important;
}

/* Hide default icons so only the gradient orb remains */
[data-testid="stChatMessageAvatarUser"] svg,
[data-testid="stChatMessageAvatarAssistant"] svg {
  display: none !important;
}

/* ===== Page2 chat canvas (custom layout) ===== */
div[data-testid="stVerticalBlock"].st-key-page2_chat_canvas,
.st-key-page2_chat_canvas {
  /* Fixed canvas height; content overflow scrolls inside this container */
  --page2-chat-pad: 14px;
  --page2-chat-btn: 44px;
  --page2-chat-gap: 10px;
  --page2-history-composer-gap: 15px;
  --page2-chat-input-height: 75px;
  /* Undo button positioning knobs (tweak these) */
  --page2-undo-gap-x: 4px;          /* space between undo and submit */
  --page2-undo-bottom-pad: 4px;     /* extra bottom offset inside canvas */
  --page2-undo-nudge-y: var(--page2-chat-btn); /* negative = move up, positive = move down */
  --page2-undo-nudge-x: 30px;       /* positive = move right */
  --page2-controls-pad-right: 18px; /* extra breathing room for text */
  /* Fill the viewport while preserving 20px breathing room above and below. */
  height: calc(100dvh - 40px) !important;
  max-height: calc(100dvh - 40px) !important;
  min-height: calc(100dvh - 40px) !important;
  box-sizing: border-box !important;
  flex: 0 0 auto !important;
  display: flex !important;
  flex-direction: column !important;
  gap: var(--page2-history-composer-gap) !important;
  position: relative !important;
  border-radius: 26px !important;
  padding: 14px !important;
  background: rgba(10, 14, 20, 0.55) !important;
  border: 1px solid rgba(255, 255, 255, 0.10) !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.06),
    0 16px 40px rgba(0, 0, 0, 0.55) !important;
  overflow: hidden !important;
  overscroll-behavior: contain !important;
}

.st-key-page2_chat_canvas > div:has(> .st-key-page2_chat_history) {
  flex: 1 1 0 !important;
  min-height: 0 !important;
  width: 100% !important;
  overflow: hidden !important;
}
.st-key-page2_chat_history {
  position: relative !important;
  width: 100% !important;
  height: 100% !important;
  min-height: 0 !important;
  max-height: 100% !important;
  display: flex !important;
  flex-direction: column !important;
  overflow-y: auto !important;
  overflow-x: hidden !important;
  padding-right: 6px !important;
  padding-bottom: 0 !important;
  justify-content: flex-start !important;
}

/*
 * A pseudo-element is a direct flex child of our keyed history container.
 * It fills only genuinely unused space above short conversations and shrinks
 * to zero once messages overflow, leaving the scroll range intact.
 */
.st-key-page2_chat_history::before {
  content: "";
  display: block;
  flex: 1 1 0 !important;
  min-height: 0 !important;
}
.st-key-page2_chat_history > div {
  flex-shrink: 0 !important;
}
.st-key-page2_chat_history > div:last-child [data-testid="stChatMessage"] {
  margin-bottom: 0 !important;
}

/* The one-shot scroll bridge must not become another flex row or add a gap. */
.st-key-page2_chat_history > div:has(> .st-key-page2_chat_scroll_trigger),
.st-key-page2_chat_scroll_trigger {
  position: absolute !important;
  inset: 0 auto auto 0 !important;
  width: 0 !important;
  height: 0 !important;
  min-height: 0 !important;
  overflow: hidden !important;
}

/* Subtle, consistent scrollbars on the actual chat-history scroller. */
.st-key-page2_chat_history::-webkit-scrollbar {
  width: 10px;
}
.st-key-page2_chat_history::-webkit-scrollbar-thumb {
  background: rgba(148, 163, 184, 0.18);
  border-radius: 999px;
  border: 2px solid rgba(0, 0, 0, 0);
  background-clip: padding-box;
}
.st-key-page2_chat_history::-webkit-scrollbar-thumb:hover {
  background: rgba(148, 163, 184, 0.26);
  border: 2px solid rgba(0, 0, 0, 0);
  background-clip: padding-box;
}

/* The composer consumes a fixed row below the flexible history region. */
.st-key-page2_chat_canvas > div:has(> .st-key-page2_chat_composer) {
  flex: 0 0 var(--page2-chat-input-height) !important;
  min-height: var(--page2-chat-input-height) !important;
  width: 100% !important;
  z-index: 5 !important;
}
.st-key-page2_chat_composer {
  position: relative !important;
  width: 100% !important;
  height: 100% !important;
}
.st-key-page2_chat_input {
  position: relative !important;
}
.st-key-page2_chat_input div[data-testid="stChatInput"] {
  position: relative !important;
  height: var(--page2-chat-input-height) !important;
  border-radius: 18px !important;
  background: rgba(10, 14, 20, 0.72) !important;
  border: 0 !important;
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.10),
    inset 0 1px 0 rgba(255, 255, 255, 0.05),
    0 10px 24px rgba(0, 0, 0, 0.50) !important;
}
.st-key-page2_chat_input div[data-testid="stChatInput"] textarea,
.st-key-page2_chat_input div[data-testid="stChatInput"] [contenteditable="true"] {
  /* Reserve right-side space for the dice and submit buttons */
  padding-right: calc(var(--page2-chat-btn) + var(--page2-chat-btn) + var(--page2-chat-gap) + var(--page2-controls-pad-right)) !important;
}

/* Shared positioning for both docked buttons (keeps them aligned at the same top) */
.st-key-page2_chat_canvas [data-testid="stChatInputSubmitButton"],
.st-key-page2_chat_dice_btn {
  position: absolute !important;
  top: 15px !important; /* (75px input - 44px button) / 2, approx with border */
  z-index: 7 !important;
  width: var(--page2-chat-btn) !important;
  margin: 0 !important;
}

/* Submit button: dock to the right edge of the composer */
.st-key-page2_chat_canvas [data-testid="stChatInputSubmitButton"] {
  right: var(--page2-controls-pad-right) !important;
  height: var(--page2-chat-btn) !important;
  min-width: var(--page2-chat-btn) !important;
  border-radius: 14px !important;
}

/* Dice button: dock immediately left of submit */
.st-key-page2_chat_dice_btn {
  right: calc(var(--page2-chat-btn) + var(--page2-chat-gap) + var(--page2-controls-pad-right)) !important;
  padding: 0 !important;
  height: var(--page2-chat-btn) !important;
}
.st-key-page2_chat_dice_btn button {
  margin: 0 !important;
  width: var(--page2-chat-btn) !important;
  height: var(--page2-chat-btn) !important;
  min-width: var(--page2-chat-btn) !important;
  min-height: var(--page2-chat-btn) !important;
  padding: 0 !important;
  border-radius: 14px !important;
  background: rgba(55, 65, 81, 0.92) !important;
  border: 1px solid rgba(255, 255, 255, 0.14) !important;
}
.st-key-page2_chat_dice_btn button:hover {
  background: rgba(75, 85, 99, 0.96) !important;
  border-color: rgba(255, 255, 255, 0.26) !important;
}
/* Undo button: visually dock to the left of the submit button */
.st-key-page2_chat_undo_btn {
  margin-top: 10px !important;
  margin-bottom: 0 !important;
}
.st-key-page2_chat_undo_btn button {
  width: 100% !important; /* match inputbar width */
  min-width: 100% !important;
  border-radius: 14px !important;
  background: rgba(220, 38, 38, 0.30) !important; /* red */
  border: 1px solid rgba(248, 113, 113, 0.55) !important;
  box-shadow: 0 10px 20px rgba(0, 0, 0, 0.45) !important;
  padding: 0 !important;
}
.st-key-page2_chat_undo_btn button:hover {
  background: rgba(220, 38, 38, 0.42) !important;
  border-color: rgba(252, 165, 165, 0.85) !important;
}
.st-key-page2_chat_undo_btn button:disabled {
  opacity: 0.45 !important;
}
/* Keep undo button visible even if icon/text structure changes across Streamlit versions */
.st-key-page2_chat_undo_btn button > div > p {
  margin: 0 !important;
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
        """,
    )
