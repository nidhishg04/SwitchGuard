# SwitchGuard — Payment Switch Resilience Engine

A hackathon-ready **simulation prototype** for payment-switch degradation monitoring, explainable recommendations, incident history, and recovery testing.

## Run locally on port 8080

### Windows PowerShell
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 127.0.0.1 --port 8080 --reload
```

Open:
- Dashboard: http://127.0.0.1:8080
- API docs: http://127.0.0.1:8080/docs
- Health: http://127.0.0.1:8080/api/health

If PowerShell blocks activation, use `.venv\Scripts\python.exe -m pip install -r requirements.txt` and `.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8080 --reload`.

## Features included
- Dark responsive fintech dashboard
- Simulated route metrics for four payment routes
- Failure injection with medium/high/critical severity
- 2.5x peak traffic burst scenario
- Reset to nominal baseline
- Simulated rerouting action for degraded routes
- Weighted failure-rate, latency, GMV, and exposure calculations
- SVG trend charts without a chart-library dependency
- Route search/filter, recommendations, and incident timeline
- API health endpoint and interactive FastAPI docs
- Explicitly labelled demo mode and Exasol configuration status

## Important honesty / safety notes
- This starter uses **in-memory simulated data**, not real bank or UPI telemetry.
- GMV-at-risk is an illustrative proxy (`route GMV × failure rate`), not a financial forecast.
- Rerouting is simulated and does not connect to payment networks.
- `EXASOL_DSN` is a status placeholder only. This starter does **not** claim a real Exasol connection. For the ExaThon, implement and test an actual Exasol driver connection, persist event data, query it, and demonstrate the query results. Keep secrets in environment variables; never commit credentials.
- Before production use, add authentication/authorization, audit logging, durable storage, schema validation, rate limits, tests, observability, and security review.

## API quick reference
- `GET /api/health`
- `GET /api/switch-metrics`
- `GET /api/metrics`
- `POST /api/inject-failure` body: `{"route_id":"sbi-upi-01","severity":"high"}`
- `POST /api/peak-burst`
- `POST /api/reset-nominal`
- `POST /api/reroute` body: `{"route_id":"sbi-upi-01"}`
- `POST /api/sync-metrics`
- `GET /api/exasol-status`

## Suggested Exasol schema
For the real database integration, a starter design could include:
- `SWITCH_EVENT(event_id, event_ts, route_id, event_type, severity, details_json)`
- `ROUTE_METRIC(metric_ts, route_id, tx_volume, failure_rate_pct, latency_ms, route_gmv, circuit_state)`
- `RECOVERY_ACTION(action_id, event_ts, source_route, target_route, moved_volume, reason, outcome)`

Use parameterized inserts, sensible timestamp handling, and retention policies. The app should show a connection/query status only after the driver successfully connects and executes a test query.
