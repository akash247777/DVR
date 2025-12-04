import argparse
import io
import math
from typing import Optional, Tuple, List, Dict

import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

from helpers import MAIN_PORT, MAIN_SERVER, UDP


def resolve_p2psrv(serial: str) -> Optional[Tuple[str, int]]:
    client = UDP(MAIN_SERVER, MAIN_PORT, debug=False)
    client.request(f"/online/p2psrv/{serial}", should_read=False)
    res = client.read(return_error=True)

    if res["code"] >= 400 or not res.get("data"):
        return None

    body = res["data"].get("body", {}) if isinstance(res.get("data"), dict) else {}
    us = body.get("US") if isinstance(body, dict) else None
    if not isinstance(us, str) or not us.strip() or ":" not in us:
        return None

    host, port_s = us.split(":", 1)
    try:
        return host, int(port_s)
    except ValueError:
        return None


def is_online(serial: str) -> bool:
    try:
        endpoint = resolve_p2psrv(serial)
        if not endpoint:
            return False

        host, port = endpoint
        p2p = UDP(host, port, debug=False)

        p2p.request(f"/probe/device/{serial}", should_read=False)
        res_probe = p2p.read(return_error=True)

        p2p.request(f"/info/device/{serial}", should_read=False)
        res_info = p2p.read(return_error=True)

        if res_probe["code"] >= 400 or res_info["code"] >= 400:
            return False

        return bool(res_info.get("data"))

    except Exception:
        return False


def _normalize_serial(value) -> str:
    if value is None:
        return ""
    # Treat NaN/NaT as empty
    try:
        if isinstance(value, float) and math.isnan(value):
            return ""
    except Exception:
        pass
    # Convert to string and strip, remove trailing .0 produced by Excel for numeric cells
    s = str(value).strip()
    if s.lower() == "nan":
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    return s


def list_offline_from_excel(xlsx_path: str, max_workers: int = 20) -> List[Dict[str, str]]:
    df = pd.read_excel(xlsx_path)

    # Normalize column names for robust matching
    normalized_cols = {c: c.strip().upper() for c in df.columns}
    df.rename(columns=normalized_cols, inplace=True)

    required = {"P2P NUMBER", "SITE", "STORE NAME"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(
            f"Missing required columns in Excel: {', '.join(sorted(missing))}"
        )

    # Keep only required columns to reduce memory
    df = df[["P2P NUMBER", "SITE", "STORE NAME"]].copy()

    # Clean serial values to strings
    df["P2P NUMBER"] = df["P2P NUMBER"].apply(_normalize_serial)

    # Build a unique set of non-empty serials to avoid redundant network calls
    unique_serials = {
        s for s in df["P2P NUMBER"].tolist() if isinstance(s, str) and s != ""
    }

    online_map: Dict[str, bool] = {}

    # Concurrently resolve online status for unique serials
    if unique_serials:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_serial = {
                executor.submit(is_online, serial): serial for serial in unique_serials
            }
            for future in as_completed(future_to_serial):
                serial = future_to_serial[future]
                try:
                    online_map[serial] = bool(future.result())
                except Exception:
                    # Treat any error as offline
                    online_map[serial] = False

    # Collect offline rows preserving original row order
    offline_rows: List[Dict[str, str]] = []
    for record in df.to_dict(orient="records"):
        serial = record.get("P2P NUMBER", "")
        is_offline = serial == "" or not online_map.get(serial, False)
        if is_offline:
            offline_rows.append(
                {
                    "P2P NUMBER": serial,
                    "SITE": record.get("SITE", ""),
                    "STORE NAME": record.get("STORE NAME", ""),
                }
            )

    return offline_rows


def _print_table(rows: List[Dict[str, str]], columns: List[str]) -> None:
    # Compute column widths
    widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            value = "" if row.get(col) is None else str(row.get(col))
            if len(value) > widths[col]:
                widths[col] = len(value)

    # Build format string
    fmt = "  ".join([f"{{:{widths[col]}}}" for col in columns])
    sep = "  ".join(["-" * widths[col] for col in columns])

    # Print header
    print(fmt.format(*columns))
    print(sep)
    # Print rows
    for row in rows:
        print(fmt.format(*(str(row.get(col, "")) for col in columns)))


def check_online(serial: str) -> int:
    try:
        endpoint = resolve_p2psrv(serial)
        if not endpoint:
            print("OFFLINE")
            return 1

        host, port = endpoint
        p2p = UDP(host, port, debug=False)

        # Probe and request info from the P2P server for this device without triggering sys.exit
        p2p.request(f"/probe/device/{serial}", should_read=False)
        res_probe = p2p.read(return_error=True)

        p2p.request(f"/info/device/{serial}", should_read=False)
        res_info = p2p.read(return_error=True)

        if res_probe["code"] >= 400 or res_info["code"] >= 400:
            print("OFFLINE")
            return 1

        if res_info.get("data"):
            print("ONLINE")
            return 0

        print("OFFLINE")
        return 1

    except Exception:
        print("OFFLINE")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check Dahua DVR online status via Easy4IPCloud"
    )
    parser.add_argument(
        "serial",
        nargs="?",
        help="Serial number of the camera/DVR (omit when using --excel)",
    )
    parser.add_argument(
        "-x",
        "--excel",
        help="Path to Excel file with columns: P2P NUMBER, SITE, STORE NAME",
    )
    args = parser.parse_args()

    if args.excel:
        offline = list_offline_from_excel(args.excel)
        # Always print header and separator for clarity
        _print_table(offline, ["P2P NUMBER", "SITE", "STORE NAME"])
        # If there are no offline rows, also print a short note to stderr-like behavior
        if not offline:
            print("No offline entries found.")
        raise SystemExit(0)

    if not args.serial:
        parser.error("Provide a serial or use --excel with an Excel file path")

    raise SystemExit(check_online(args.serial))


# venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
# http://127.0.0.1:8000/
