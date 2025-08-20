import os
import sys
import time
import subprocess
from pathlib import Path

import streamlit as st

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

import socket

def get_local_ip():
    try:
        # Connect to a remote address to determine local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"

BACKEND_HOST = get_local_ip()
BACKEND_PORT = int(os.environ.get("DVR_BACKEND_PORT", "8000"))
BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
PROJECT_ROOT = Path(__file__).parent.resolve()
INDEX_HTML = PROJECT_ROOT / "web" / "index.html"


def is_backend_up(timeout_seconds: float = 0.5) -> bool:
    if requests is None:
        return False
    try:
        r = requests.get(f"{BACKEND_URL}/api/stats", timeout=timeout_seconds)
        return r.ok
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def start_backend_once() -> subprocess.Popen | None:
    if is_backend_up():
        return None

    # Spawn uvicorn as a child process so its event loop and background thread run independently
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "server:app",
        "--host",
        BACKEND_HOST,
        "--port",
        str(BACKEND_PORT),
        # Do not use --reload inside Streamlit to avoid file watcher conflicts
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )

    # Wait for the API to become responsive
    deadline = time.time() + 20
    while time.time() < deadline:
        if is_backend_up():
            break
        time.sleep(0.3)
    return proc


def inject_base_href(html_text: str, base_url: str) -> str:
    # Ensure all relative references (/api, /static, favicon, etc.) resolve to the backend
    lower = html_text.lower()
    head_idx = lower.find("<head>")
    if head_idx == -1:
        # Fallback: prepend base to the very beginning (harmless)
        return f'<base href="{base_url}/">' + html_text
    insert_at = head_idx + len("<head>")
    return html_text[:insert_at] + f"\n    <base href=\"{base_url}/\">\n" + html_text[insert_at:]


def render_frontend():
    # Preferred: embed the running backend UI directly for 1:1 fidelity
    if is_backend_up():
        # Add a query parameter to signal that this is coming from Streamlit
        # This will be used in index.html to prevent the video from playing twice
        streamlit_url = f"{BACKEND_URL}?streamlit=true"
        st.components.v1.iframe(streamlit_url, height=900)
        # Removed the "Open in a new tab" link
        return

    # Fallback: inline HTML with injected base to point at backend URL
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
