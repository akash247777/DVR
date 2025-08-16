from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from pathlib import Path
import io
import threading
import time

from check_online import is_online, _normalize_serial


BASE_DIR = Path(__file__).parent
EXCEL_PATH = BASE_DIR / "P2P1.xlsx"
WEB_DIR = BASE_DIR / "web"


class DataStore:
    def __init__(self, excel_path: Path) -> None:
        self.excel_path = excel_path
        self.lock = threading.RLock()
        self.df: Optional[pd.DataFrame] = None
        self.rows: List[Dict[str, str]] = []
        self.online_map: Dict[str, bool] = {}
        self.last_scan_epoch: Optional[float] = None

    def load_excel(self) -> None:
        with self.lock:
            if not self.excel_path.exists():
                raise FileNotFoundError(f"Excel file not found: {self.excel_path}")
            df = pd.read_excel(self.excel_path)
            normalized_cols = {c: c.strip().upper() for c in df.columns}
            df.rename(columns=normalized_cols, inplace=True)
            required = {"P2P NUMBER", "SITE", "STORE NAME"}
            missing = required - set(df.columns)
            if missing:
                raise RuntimeError(
                    f"Missing required columns in Excel: {', '.join(sorted(missing))}"
                )
            df = df[["P2P NUMBER", "SITE", "STORE NAME"]].copy()
            # Normalize P2P to string-like for consistency
            df["P2P NUMBER"] = df["P2P NUMBER"].apply(_normalize_serial)
            self.df = df
            # Build in-memory rows list
            self.rows = [
                {
                    "P2P NUMBER": str(row.get("P2P NUMBER", "")),
                    "SITE": str(row.get("SITE", "")),
                    "STORE NAME": str(row.get("STORE NAME", "")),
                }
                for row in df.to_dict(orient="records")
            ]

    def scan_statuses(self, max_workers: int = 20) -> None:
        with self.lock:
            # Build unique serials
            unique_serials = {
                r["P2P NUMBER"]
                for r in self.rows
                if isinstance(r.get("P2P NUMBER"), str) and r.get("P2P NUMBER")
            }
        online_map: Dict[str, bool] = {}
        if unique_serials:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_serial = {executor.submit(is_online, s): s for s in unique_serials}
                for fut in as_completed(future_to_serial):
                    s = future_to_serial[fut]
                    try:
                        online_map[s] = bool(fut.result())
                    except Exception:
                        online_map[s] = False
        with self.lock:
            self.online_map = online_map
            self.last_scan_epoch = time.time()

    def get_stats(self) -> Dict[str, int]:
        with self.lock:
            total = len(self.rows)
            online = 0
            offline = 0
            for r in self.rows:
                serial = r.get("P2P NUMBER", "")
                is_on = serial != "" and self.online_map.get(serial, False)
                if is_on:
                    online += 1
                else:
                    offline += 1
            return {
                "total": total,
                "online": online,
                "offline": offline,
                "lastUpdated": self.last_scan_epoch,
            }

    def list_by_status(self, status: str) -> List[Dict[str, str]]:
        with self.lock:
            def is_online_row(r: Dict[str, str]) -> bool:
                s = r.get("P2P NUMBER", "")
                return s != "" and self.online_map.get(s, False)

            if status == "all":
                return list(self.rows)
            if status == "online":
                return [r for r in self.rows if is_online_row(r)]
            if status == "offline":
                return [r for r in self.rows if not is_online_row(r)]
            raise ValueError("Invalid status")

    def search_site(self, site: str) -> Optional[Dict[str, str]]:
        site_str = str(site).strip()
        with self.lock:
            found = None
            for r in self.rows:
                if str(r.get("SITE", "")).strip() == site_str:
                    found = dict(r)
                    break
            if not found:
                return None
            serial = found.get("P2P NUMBER", "")
            is_on = serial != "" and self.online_map.get(serial, False)
            found["status"] = "online" if is_on else "offline"
            return found

    def update_p2p(self, site: str, new_p2p: str) -> bool:
        site_str = str(site).strip()
        new_serial = _normalize_serial(new_p2p)
        with self.lock:
            if self.df is None:
                raise RuntimeError("Excel not loaded")
            # Find and update in df
            mask = self.df["SITE"].astype(str).str.strip() == site_str
            if not mask.any():
                return False
            self.df.loc[mask, "P2P NUMBER"] = new_serial
            # Persist back to Excel
            self.df.to_excel(self.excel_path, index=False)
            # Update in-memory rows
            for r in self.rows:
                if str(r.get("SITE", "")).strip() == site_str:
                    r["P2P NUMBER"] = new_serial
            # Invalidate status for that serial; require refresh to recompute
            if new_serial:
                self.online_map.pop(new_serial, None)
            self.last_scan_epoch = None
            return True


store = DataStore(EXCEL_PATH)
store.load_excel()

app = FastAPI(title="DVR Status Dashboard")

# If you plan to serve the UI from same app, CORS isn't strictly necessary, but keep permissive for flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _scanner_loop() -> None:
    # Initial scan immediately, then every 2 minutes
    try:
        store.scan_statuses()
    except Exception:
        pass
    while True:
        try:
            time.sleep(10)
            store.scan_statuses()
        except Exception:
            # Keep loop alive even if a scan fails
            continue


@app.on_event("startup")
def _start_background_scanner() -> None:
    t = threading.Thread(target=_scanner_loop, name="scanner_loop", daemon=True)
    t.start()


@app.get("/api/stats")
def api_stats():
    # Return current aggregated stats; background task updates every 2 minutes
    return store.get_stats()


@app.get("/api/dvrs")
def api_dvrs(status: str = Query("all", pattern="^(all|online|offline)$")):
    data = store.list_by_status(status)
    return {"items": data}


@app.get("/api/search")
def api_search(site: str = Query(...)):
    res = store.search_site(site)
    if not res:
        raise HTTPException(status_code=404, detail="SITE not found")
    return res


@app.post("/api/update-p2p")
def api_update_p2p(payload: Dict[str, str]):
    site = payload.get("site")
    new_p2p = payload.get("p2pNumber")
    if not site or new_p2p is None:
        raise HTTPException(status_code=400, detail="Missing 'site' or 'p2pNumber'")
    updated = store.update_p2p(site, new_p2p)
    if not updated:
        raise HTTPException(status_code=404, detail="SITE not found")
    return {"ok": True}


@app.post("/api/refresh")
def api_refresh():
    # Perform an on-demand rescan now and return updated stats
    store.scan_statuses()
    return {"ok": True, **store.get_stats()}


@app.get("/api/download.csv")
def api_download_csv(status: str = Query("all", pattern="^(all|online|offline)$")):
    rows = store.list_by_status(status)
    output = io.StringIO()
    # Write CSV header
    output.write("P2P NUMBER,SITE,STORE NAME\n")
    for r in rows:
        p2p = (r.get("P2P NUMBER", "") or "").replace(",", " ")
        site = (r.get("SITE", "") or "").replace(",", " ")
        store_name = (r.get("STORE NAME", "") or "").replace(",", " ")
        output.write(f"{p2p},{site},{store_name}\n")
    output.seek(0)
    headers = {"Content-Disposition": f"attachment; filename=dvrs_{status}.csv"}
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)


# Serve frontend
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def root():
    index = WEB_DIR / "index.html"
    if not index.exists():
        return JSONResponse({"message": "UI not found. API is running."})
    return FileResponse(str(index))


