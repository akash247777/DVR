import os
import sys
import time
import subprocess
import socket
from pathlib import Path

import streamlit as st

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


# Detect deployment (Streamlit Cloud, HuggingFace, etc.)
DEPLOYED = bool(os.environ.get("STREAMLIT_RUNTIME") or os.environ.get("SPACE_ID"))

PROJECT_ROOT = Path(__file__).parent.resolve()
INDEX_HTML = PROJECT_ROOT / "web" / "index.html"

# Local vs hosted configuration
if DEPLOYED:
    BACKEND_HOST = "0.0.0.0"
    BACKEND_PORT = int(os.environ.get("DVR_BACKEND_PORT", "8000"))
    BACKEND_URL = ""  # relative URLs (Streamlit iframe/static handles it)
else:
    BACKEND_HOST = "0.0.0.0"
    BACKEND_PORT = int(os.environ.get("DVR_BACKEND_PORT", "8000"))
    # Use LAN IP so phone on same Wi-Fi can connect
    local_ip = socket.gethostbyname(socket.gethostname())
    BACKEND_URL = f"http://{local_ip}:{BACKEND_PORT}"


def is_backend_up(timeout_seconds: float = 0.5) -> bool:
    if not BACKEND_URL or requests is None:
        return False
    try:
        r = requests.get(f"{BACKEND_URL}/api/stats", timeout=timeout_seconds)
        return r.ok
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def start_backend_once() -> subprocess.Popen | None:
    if DEPLOYED:
        # Start FastAPI server directly in a background thread
        import threading
        import uvicorn
        from server import app  # Assuming your FastAPI app is in server.py as `app`

        def run_uvicorn():
            uvicorn.run(
                app,
                host=BACKEND_HOST,
                port=BACKEND_PORT,
                log_level="info"
            )

        thread = threading.Thread(target=run_uvicorn, daemon=True)
        thread.start()
        time.sleep(2)  # Give it a moment to start
        return None  # No subprocess
    else:
        # Existing local subprocess logic
        if is_backend_up():
            return None
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "server:app",
            "--host",
            BACKEND_HOST,
            "--port",
            str(BACKEND_PORT),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if is_backend_up():
                break
            time.sleep(0.3)
        return proc


def inject_base_href(html_text: str, base_url: str) -> str:
    if not base_url:
        return html_text
    lower = html_text.lower()
    head_idx = lower.find("<head>")
    if head_idx == -1:
        return f'<base href="{base_url}/">' + html_text
    insert_at = head_idx + len("<head>")
    return html_text[:insert_at] + f'\n    <base href="{base_url}/">\n' + html_text[insert_at:]


def render_frontend():
    if not DEPLOYED and is_backend_up() and BACKEND_URL:
        streamlit_url = f"{BACKEND_URL}?streamlit=true"
        st.components.v1.iframe(streamlit_url, height=900)
        return

    if INDEX_HTML.exists():
        html_text = INDEX_HTML.read_text(encoding="utf-8")
        html_text = inject_base_href(html_text, BACKEND_URL)
        st.components.v1.html(html_text, height=900, scrolling=True)
    else:
        st.error("web/index.html not found")


def main():
    st.set_page_config(page_title="DVR Status Dashboard", layout="wide")
    with st.spinner("Starting backend..."):
        start_backend_once()
    render_frontend()


if __name__ == "__main__":
    main()
