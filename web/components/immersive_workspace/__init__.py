"""Native Page2 widgets with an immersive, browser-local presentation shell."""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from pathlib import Path

import streamlit as st
from streamlit import runtime

from app.services import media_store

_ROOT = Path(__file__).parent

_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _sniff_image_mime(data: bytes) -> str | None:
    for signature, mime in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _decode_image_base64(value: str) -> tuple[bytes, str] | None:
    """Accept both raw base64 and provider data URIs, keeping the true image type."""
    text = str(value or "").strip()
    if not text:
        return None
    mime = ""
    if text.lower().startswith("data:"):
        header, _, text = text.partition(",")
        mime = header[5:].split(";", 1)[0].strip().lower()
    try:
        data = base64.b64decode(text)
    except Exception:
        return None
    if not data:
        return None
    return data, _sniff_image_mime(data) or (mime if mime.startswith("image/") else "image/png")


def background_source(selected, url: str, visible: bool) -> dict:
    """Serve cached bytes through Streamlit; never expose filesystem paths or credentials."""
    if not visible:
        return {"id": "", "kind": "none", "src": ""}
    if url:
        return {"id": "url:" + url, "kind": "image", "src": url}
    if selected is None:
        return {"id": "", "kind": "none", "src": ""}
    kind = getattr(selected.media_kind, "value", selected.media_kind)
    source = selected.video_url_string if kind == "video" else selected.image_url_string
    path = media_store.local_path(selected)
    data = None
    mime = ""
    if path:
        data = str(path)
        mime = mimetypes.guess_type(str(path))[0] or ("video/mp4" if kind == "video" else "image/png")
    elif kind == "image" and selected.image_data_base64:
        decoded = _decode_image_base64(selected.image_data_base64)
        if decoded:
            data, mime = decoded
    if data is not None:
        if runtime.exists():
            try:
                source = runtime.get_instance().media_file_mgr.add(data, mime, "dnd-background")
                data = None
            except Exception:
                pass
        if isinstance(data, bytes):
            source = "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")
    return {"id": selected.id, "kind": kind, "src": source or ""}


def _assets() -> tuple[str, str, str]:
    css = (_ROOT / "workspace.css").read_text()
    js = (_ROOT / "workspace.js").read_text()
    version = hashlib.sha1((css + "\n" + js).encode("utf-8")).hexdigest()[:12]
    return css, js, version


def render_workspace_styles() -> None:
    """Inject page2 styles before any page2 widget is streamed.

    Streamlit paints elements in order. Keeping the stylesheet at the top of
    the page avoids a first frame laid out with unrelated global CSS.
    """
    css, _, _ = _assets()
    # Use the same renderer as chat messages so its lazy-loaded bundle cannot
    # paint the chat before the separate st.html renderer has installed CSS.
    st.markdown("<style>" + css + "</style>", unsafe_allow_html=True)


def _install_script(js: str) -> str:
    source = json.dumps(js).replace("<", "\\u003c")
    return ("const script=host.document.createElement('script');script.textContent="
            + source + ";host.document.head.appendChild(script);script.remove();")


def prepare_workspace(*, active: bool) -> None:
    """Set the presentation mode before loading records or building sidebars."""
    if active and not st.session_state.get("page2_workspace_active", False):
        st.session_state.pop("page2_history_window", None)
        st.session_state.pop("page2_workspace_mount_scope", None)
    st.session_state["page2_workspace_active"] = active
    st.session_state["page2_workspace_lazy"] = active
    render_workspace_styles()
    _, js, version = _assets()
    bridge = "const host=window.parent;" + _install_script((_ROOT / "navigation.js").read_text())
    if active:
        bridge += (
            "host.__dndImmersiveBuild=" + json.dumps(version) + ";"
            "if(host.__dndImmersive&&host.__dndImmersive.version!==host.__dndImmersiveBuild){"
            "host.__dndImmersive.dispose();}"
            "if(!host.__dndImmersive){" + _install_script(js) + "}"
            "host.__dndImmersive.begin();"
        )
    else:
        bridge += "host.__dndImmersive?.dispose();"
    with st.container(key="page2_game_mount" if active else "page2_navigation_bridge"):
        st.iframe("<script>" + bridge + "</script>", height=1)


def render_workspace(*, scope: str, record_id: str, selected, url: str, visible: bool, veil_opacity: int,
                     history: dict | None = None, sections: list | None = None) -> None:
    _, js, version = _assets()
    config = {
        "scope": scope, "recordId": record_id,
        "background": background_source(selected, url, visible),
        "veil": max(0, min(98, veil_opacity)) / 100,
        "history": history or {}, "sections": sections or [],
    }
    payload = json.dumps(config, ensure_ascii=False).replace("<", "\\u003c")
    # Bind long-lived event handlers in the host document's realm. Callbacks
    # created in a rerendered iframe stop receiving browser events once it dies.
    source = json.dumps(js).replace("<", "\\u003c")
    bridge = (
        "const host=window.parent;"
        "host.__dndImmersiveBuild=" + json.dumps(version) + ";"
        "if(host.__dndImmersive&&host.__dndImmersive.version!==host.__dndImmersiveBuild){"
        "try{host.__dndImmersive.dispose();}catch(e){}host.__dndImmersive=null;}"
        "if(!host.__dndImmersive){"
        "const script=host.document.createElement('script');script.textContent=" + source + ";"
        "host.document.head.appendChild(script);script.remove();}"
        "host.__dndImmersive.update(" + payload + ");"
    )
    with st.container(key="page2_immersive_bridge"):
        st.iframe("<script>" + bridge + "</script>", height=1)
