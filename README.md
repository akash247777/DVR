## DVR Status Dashboard

Real-time monitoring of Dahua DVRs via Easy4IPCloud, with a FastAPI backend and a static web UI. The app reads an Excel workbook of devices, periodically checks their online status through Dahua's cloud, exposes a REST API, and serves a simple dashboard.

# DEMO 📽 🎥

https://github.com/user-attachments/assets/33844588-0b2a-4f7d-a16d-3e30349b30e3

# Flow Diagram

<img width="908" height="1227" alt="diagram-export-18-1-2026-8_02_57-pm" src="https://github.com/user-attachments/assets/82ec4145-9487-495c-910f-e4a333ee27eb" />


### Key features
- **Excel-driven inventory**: Loads devices from `P2P1.xlsx` with columns `P2P NUMBER`, `SITE`, `STORE NAME`.
- **Background status scanner**: Periodically probes devices via Easy4IPCloud and caches results in-memory.
- **REST API**: Query aggregate stats, list devices by status, search by site, export CSV, refresh scans, and update P2P numbers.
- **Static web UI**: If `web/index.html` exists, it is served at `/` with static assets under `/static`.

### Repository layout
- `server.py`: FastAPI app, background scanner, REST endpoints, static file serving
- `check_online.py`: CLI for single-device or bulk (Excel) status checks
- `helpers.py`: Low-level UDP/WSSE protocol helpers for Easy4IPCloud
- `P2P1.xlsx`: Input Excel file of devices (not versioned typically)
- `web/`: Frontend assets (served if present)
- `requirements.txt`: Python dependencies

## Architecture

### Data ingestion
- On startup, `server.py` loads `P2P1.xlsx`. Column names are normalized to uppercase, and only `P2P NUMBER`, `SITE`, `STORE NAME` are retained.
- P2P serial values are normalized to strings; numeric cells like `123456.0` are converted to `123456`.

### Background scanning
- A daemon thread runs a loop that refreshes device statuses on a cadence (default ~10 seconds in current code). Results are cached in-memory, keyed by `P2P NUMBER`.
- Status lookups are executed concurrently using a thread pool to minimize total latency.

### Online detection
- For each serial, the app resolves the target P2P service endpoint via `GET /online/p2psrv/{serial}` on `www.easy4ipcloud.com:8800`.
- It then probes and fetches device info via UDP-based requests; a device is considered online when a valid info response is returned.

### Web/API
- FastAPI serves JSON endpoints under `/api/*` and optionally serves the static UI from `web/`.

## Getting started

### Prerequisites
- Python 3.10+
- Network egress to `www.easy4ipcloud.com:8800`
- An Excel workbook `P2P1.xlsx` placed in the project root with required columns

### Installation
```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### Run the API server
```bash
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

- Open the UI at `http://localhost:8000/` if `web/index.html` exists; otherwise the API root returns a basic JSON message.

### CLI usage (quick checks)
Check a single serial:
```bash
python check_online.py <SERIAL>
```

Check from an Excel file and print offline entries:
```bash
python check_online.py --excel P2P1.xlsx
```

## Excel file format

- The workbook must contain a sheet with the following columns (case-insensitive; they are normalized on load):
  - `P2P NUMBER`: Dahua device serial (string or number)
  - `SITE`: Unique site identifier
  - `STORE NAME`: Human-friendly store name
- Only these three columns are used by the backend; any extra columns are ignored when loading.

## REST API

Base URL: `http://{host}:{port}` (default `http://localhost:8000`)

### GET `/api/stats`
Returns aggregate counts and last update epoch.
```json
{
  "total": 123,
  "online": 100,
  "offline": 23,
  "lastUpdated": 1712345678.123
}
```

### GET `/api/dvrs?status=all|online|offline`
Returns device rows filtered by status.
```json
{ "items": [ {"P2P NUMBER":"...","SITE":"...","STORE NAME":"..."} ] }
```

### GET `/api/search?site={SITE}`
Finds a single row by exact `SITE` and includes computed `status` field.
```json
{ "P2P NUMBER":"...","SITE":"...","STORE NAME":"...","status":"online" }
```

### POST `/api/update-p2p`
Update the `P2P NUMBER` for a given `SITE` in both memory and Excel; invalidates cached status.
```json
{ "site": "SITE-001", "p2pNumber": "NEW_SERIAL" }
```

Responses: `{ "ok": true }` or `404` if site not found.

### POST `/api/refresh`
Triggers an immediate rescan and returns updated stats.
```json
{ "ok": true, "total": 123, "online": 101, "offline": 22, "lastUpdated": 1712345800.456 }
```

### GET `/api/download.csv?status=all|online|offline`
Streams a CSV attachment of filtered rows with header: `P2P NUMBER,SITE,STORE NAME`.

### cURL examples
```bash
curl -s http://localhost:8000/api/stats | jq
curl -s "http://localhost:8000/api/dvrs?status=offline" | jq
curl -s "http://localhost:8000/api/search?site=SITE-001" | jq
curl -s -X POST http://localhost:8000/api/update-p2p \
  -H "Content-Type: application/json" \
  -d '{"site":"SITE-001","p2pNumber":"ABC123456"}' | jq
curl -s -X POST http://localhost:8000/api/refresh | jq
curl -OJ "http://localhost:8000/api/download.csv?status=offline"
```

## Configuration

- Scanner concurrency: controlled internally by a thread pool (default up to 20 workers). Adjust in code if needed (`scan_statuses(max_workers=...)`).
- Scan interval: controlled in `server.py` scanner loop (default sleep ~10s between scans). Adjust as desired.
- CORS: permissive by default to simplify hosting UI separately; tweak in `server.py` middleware setup.

## Dependencies

- FastAPI, Uvicorn, pandas, openpyxl, cryptography, xmltodict, aiofiles
- See `requirements.txt` for the exact list.

## Security and privacy

- The low-level integration uses credentials/constants in `helpers.py` (e.g., username key material). Treat these as secrets and rotate/change handling if distributing publicly.
- The app does not persist PII beyond the contents of `P2P1.xlsx`. Handle the Excel file in accordance with your data policies.

## Troubleshooting

- "Excel file not found": ensure `P2P1.xlsx` exists in the repository root.
- "Missing required columns": verify the sheet has `P2P NUMBER`, `SITE`, `STORE NAME` (case-insensitive).
- All devices show offline: check network/firewall egress to `www.easy4ipcloud.com:8800`.
- Slow scans or timeouts: reduce `max_workers`, increase socket timeout in `helpers.py`, or increase scan interval.
- Status doesn’t change immediately after updating a P2P number: invoke `POST /api/refresh` or wait for the next scheduled scan.

## Development

- Linting/formatting/testing are not included by default; add your preferred tooling.
- The UI in `web/` is served at `/` when present, and static assets are available at `/static`.
