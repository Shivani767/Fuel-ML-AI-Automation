# Fuel ML / AI Automation

Fleet fuel **fill** and **theft** detection from FuelArk telematics.

- **Backend:** Flask API (`:5001`) — live fleet snapshot, session detector v3 + RandomForest, 30‑minute alert pipeline  
- **Frontend:** React + Vite (`:5173`) — Fleet Live Monitor, Live Map graphs, Fuel Alerts  

Precision gates suppress driving/slope noise so markers stay closer to real pump fills and parked thefts (not ignition-on slosh).

---

## Clone

```bash
git clone https://github.com/Shivani767/Fuel-ML-AI-Automation.git
cd Fuel-ML-AI-Automation
```

If the repo is private, sign in with GitHub first (`gh auth login` or an SSH key / personal access token).

---

## Prerequisites

- **Python** 3.9+  
- **Node.js** 18+ and npm  
- A **FuelArk API key** (and optional web credentials) for live data  

---

## Install (localhost)

### 1. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env
```

Edit `.env` and set at least:

```bash
FUELARK_API_KEY=your_api_key_here
FUELARK_API_URL=https://api1.fuelark.com/api/FuelConsumption/DeviceFuelConsumption
FUELARK_BASE_URL=https://web1.fuelark.com
# optional if your tenant uses web login:
# FUELARK_USERNAME=
# FUELARK_PASSWORD=
```

Never commit `.env`.

### 3. Frontend

```bash
cd frontend
npm install
cd ..
```

---

## Run locally

Use **two terminals**.

**Terminal A — backend**

```bash
source .venv/bin/activate
python -m backend.app
```

Wait until you see something like `Backend listening on http://0.0.0.0:5001` (first load of historical/live overlay can take a minute).

**Terminal B — frontend**

```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Open:

| App | URL |
|-----|-----|
| UI | http://127.0.0.1:5173 |
| API health | http://127.0.0.1:5001/api/health |

Vite proxies `/api` to the Flask backend (see `frontend/vite.config.js`).

---

## How to test

### Quick API checks

```bash
curl -s http://127.0.0.1:5001/api/health
curl -s "http://127.0.0.1:5001/api/fleet/summary?refresh=0"
```

After a cold start, click **Refresh now** on the fleet page (or wait ~2 minutes) so FuelArk live data warms the cache. Until then, Active/Running can read `0`.

### UI smoke test

1. **Fleet Live Monitor** (`/`) — Total / Active / Running / Inactive; map + device list.  
2. Open a vehicle → **Live Map** (`/live`) — fuel (liters) chart, ignition bands, fill/theft markers.  
3. **Fuel Alerts** (`/alerts`) — 30‑min agent digest; **View graph** / **Full report** on each case.  
4. **Admin** (`/admin`) — add a vehicle by IMEI if needed.

### Unit tests

```bash
source .venv/bin/activate
python -m unittest tests.test_alert_gate -v
```

### Alert review sheet (optional)

After the alert pipeline has written a day CSV:

```bash
.venv/bin/python tools/build_alert_review_sheet.py --day YYYY-MM-DD
# edit fuel_data/alerts/reviews/YYYY-MM-DD_review.csv → verdict CORRECT / WRONG / UNSURE
.venv/bin/python tools/score_alert_review.py --day YYYY-MM-DD
```

---

## Project layout (short)

```
backend/           Flask app, API routes, detection + alerts
frontend/          React UI
fuel-theft-detector/   Session detector v3
fuel_data/         Telemetry / alerts CSVs (large dumps are gitignored)
tools/             Review sheet + ops scripts
tests/             Unit tests
docs/ARCHITECTURE.md
```

---

## Notes

- Large monthly telemetry JSON dumps are **gitignored**; live fetch from FuelArk populates memory / `live_fuel_data.json` when configured.  
- Collaborators: ask the repo owner for access if clone returns 404 on a private repo.  
- More detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
