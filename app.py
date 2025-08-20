import os
import sys
import time
import subprocess
import socket
from pathlib import Path
import streamlit as st

try:
    import requests
except Exception:
    requests = None

# === Detect Deployment Environment ===
DEPLOYED = bool(os.environ.get("STREAMLIT_RUNTIME") or os.environ.get("SPACE_ID"))

# === Project & File Setup ===
PROJECT_ROOT = Path(__file__).parent.resolve()
INDEX_HTML = PROJECT_ROOT / "web" / "index.html"

# === Backend Configuration ===
BACKEND_HOST = "0.0.0.0"
BACKEND_PORT = int(os.environ.get("DVR_BACKEND_PORT", 8000))

if DEPLOYED:
    BACKEND_URL = ""  # Use relative URLs in production
else:
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "127.0.0.1"
    BACKEND_URL = f"http://{local_ip}:{BACKEND_PORT}"


# === Health Check: Is Backend Alive? ===
def is_backend_up(timeout_seconds: float = 0.5) -> bool:
    if not BACKEND_URL or requests is None:
        return False
    try:
        r = requests.get(f"{BACKEND_URL}/api/stats", timeout=timeout_seconds)
        return r.ok
    except Exception:
        return False


# === Start Backend: Subprocess (Local) or Thread (Deployed) ===
@st.cache_resource(show_spinner=False)
def start_backend_once():
    if DEPLOYED:
        # === Deployed: Run FastAPI in a background thread ===
        import threading

        def run_uvicorn():
            try:
                import uvicorn
                from server import app  # Must exist: FastAPI app in server.py
                uvicorn.run(
                    app,
                    host=BACKEND_HOST,
                    port=BACKEND_PORT,
                    log_level="info"
                )
            except Exception as e:
                st.error(f"❌ Failed to start FastAPI server: {e}")
                st.stop()

        thread = threading.Thread(target=run_uvicorn, daemon=True)
        thread.start()

        # Wait for server to respond
        st.info("🚀 Starting backend API...")
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if requests and requests.get(f"http://127.0.0.1:{BACKEND_PORT}/api/stats", timeout=1).ok:
                    st.success("✅ Backend is ready!")
                    return None
            except Exception:
                time.sleep(0.5)
        st.error("💥 Backend failed to start after 15 seconds.")
        st.stop()

    else:
        # === Local: Use subprocess ===
        if is_backend_up():
            return None

        st.info("🔧 Starting local backend...")

        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "server:app",
            "--host", BACKEND_HOST,
            "--port", str(BACKEND_PORT),
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
                st.success("✅ Local backend is running.")
                return proc
            time.sleep(0.3)

        st.error("💥 Failed to start local backend.")
        st.stop()


# === Inject <base href> for correct asset routing ===
def inject_base_href(html_text: str, base_url: str) -> str:
    if not base_url:
        return html_text

    lower = html_text.lower()
    head_idx = lower.find("<head>")
    if head_idx == -1:
        return f'<base href="{base_url}/">' + html_text

    insert_at = head_idx + len("<head>")
    return (
        html_text[:insert_at]
        + f'\n    <base href="{base_url}/">\n'
        + html_text[insert_at:]
    )


# === Render Frontend ===
def render_frontend():
    # Optional: If you want to iframe to backend in local dev
    if not DEPLOYED and is_backend_up() and BACKEND_URL:
        streamlit_url = f"{BACKEND_URL}?streamlit=true"
        st.components.v1.iframe(streamlit_url, height=900)
        return

    # Normal: Serve index.html with base href
    if INDEX_HTML.exists():
        html_text = INDEX_HTML.read_text(encoding="utf-8")
        html_text = inject_base_href(html_text, BACKEND_URL)
        st.components.v1.html(html_text, height=900, scrolling=True)
    else:
        st.error("❌ `web/index.html` not found. Please check your file structure.")


# === Main App ===
def main():
    st.set_page_config(
        page_title="DVR Status Dashboard",
        layout="wide",
        initial_sidebar_state="collapsed"
    )

    with st.spinner("🔧 Starting backend..."):
        start_backend_once()

    render_frontend()


if __name__ == "__main__":
    main()
