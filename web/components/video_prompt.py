"""Small UI bridges for the native video prompt editor and actionable toast."""

import streamlit as st
import streamlit.components.v1 as components


def render_toast_style():
    st.html("""<style>
    .st-key-page2_video_story_toast {
      position: fixed; top: 4rem; right: 1rem; z-index: 999;
      width: min(380px, calc(100vw - 2rem)); padding: 1rem;
      border: 1px solid var(--st-border-color, #666);
      border-radius: .75rem;
      background: var(--st-background-color, #17191f);
      box-shadow: 0 8px 30px #0005;
    }
    .st-key-_page2_video_prompt textarea {
      min-height: 68px; max-height: 160px; resize: none;
      overflow-y: auto;
    }
    </style>""")


def render_editor_sizing():
    components.html("""<script>
    (() => {
      const host = window.parent, doc = host.document;
      host.__dndVideoPromptSizing?.disconnect();
      const selector = '.st-key-_page2_video_prompt textarea';
      const resize = (input) => {
        input.style.height = '0px';
        const height = Math.max(68, Math.min(160, input.scrollHeight + 2));
        input.style.height = height + 'px';
      };
      const sync = () => {
        const input = doc.querySelector(selector);
        if (!input) return;
        resize(input);
        if (!input.dataset.videoSizing) {
          input.dataset.videoSizing = 'true';
          input.addEventListener('input', () => resize(input));
        }
      };
      const observer = new host.MutationObserver(sync);
      observer.observe(doc.body, {childList: true, subtree: true});
      host.__dndVideoPromptSizing = observer;
      sync();
    })();
    </script>""", height=0)
