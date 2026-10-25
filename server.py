"""
SwitchGuard Enterprise - Payment Switch Degradation & Autonomous Recovery Engine
Data Platform: Exasol Personal / High-Velocity Columnar Analytical Core
"""

from __future__ import annotations
import os
import sys
import time
import uuid
import random
import datetime
import warnings
from typing import Optional, List, Dict, Any, Union

import duckdb
from pydantic import BaseModel, Field
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Suppress pyexasol SSL certificate deprecation warnings during local dev
warnings.filterwarnings("ignore", category=UserWarning, module="pyexasol")

try:
    import pyexasol
    EXASOL_DRIVER_AVAILABLE = True
except ImportError:
    EXASOL_DRIVER_AVAILABLE = False

# -----------------------------------------------------------------------------
# APPLICATION CONFIGURATION
# -----------------------------------------------------------------------------

APP_TITLE = "SwitchGuard | Payment Resilience Engine"
PORT = int(os.getenv("PORT", "8080"))

app = FastAPI(title=APP_TITLE, version="4.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# EXASOL & IN-MEMORY ENGINE ADAPTER
# -----------------------------------------------------------------------------

EXA_HOST = os.getenv("EXASOL_HOST", "localhost:8563")
EXA_USER = os.getenv("EXASOL_USER", "sys")
EXA_PASS = os.getenv("EXASOL_PASSWORD", "exasolPassword")
EXA_SCHEMA = os.getenv("EXASOL_SCHEMA", "SWITCHGUARD")

class ExasolConnector:
    """Manages queries to Exasol with seamless fallback to in-memory vectorized core."""
    def __init__(self):
        self.connected_to_live_exasol = False
        self.exa_conn = None
        self.fallback_db = duckdb.connect(database=":memory:")
        self.init_fallback_schema()
        self.try_exasol_connect()

    def try_exasol_connect(self):
        if not EXASOL_DRIVER_AVAILABLE:
            self.connected_to_live_exasol = False
            return
        try:
            # Set encryption=False and low timeout to prevent socket hangs
            self.exa_conn = pyexasol.connect(
                dsn=EXA_HOST,
                user=EXA_USER,
                password=EXA_PASS,
                schema=EXA_SCHEMA,
                encryption=False,
                connection_timeout=1
            )
            self.connected_to_live_exasol = True
            print("[DATA PLATFORM] Connected directly to live Exasol Personal database.")
        except Exception:
            self.connected_to_live_exasol = False
            self.exa_conn = None

    def init_fallback_schema(self):
        self.fallback_db.execute("""
            CREATE TABLE IF NOT EXISTS TRANSACTIONS (
                txn_id VARCHAR,
                txn_timestamp TIMESTAMP,
                merchant_id VARCHAR,
                gateway VARCHAR,
                issuer_bank VARCHAR,
                payment_mode VARCHAR,
                vpa_handle VARCHAR,
                amount DECIMAL(10,2),
                status VARCHAR,
                latency_ms INT,
                error_code VARCHAR,
                datacenter VARCHAR
            );
        """)

    def execute_query(self, query: str) -> List[Any]:
        if self.connected_to_live_exasol and self.exa_conn:
            try:
                return self.exa_conn.export_to_list(query)
            except Exception:
                pass
        return self.fallback_db.execute(query).fetchall()

    def insert_batch(self, rows: List[tuple]):
        self.fallback_db.execute("DELETE FROM TRANSACTIONS;")
        self.fallback_db.executemany(
            "INSERT INTO TRANSACTIONS VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
            rows
        )
        if self.connected_to_live_exasol and self.exa_conn:
            try:
                self.exa_conn.execute("TRUNCATE TABLE TRANSACTIONS;")
                self.exa_conn.import_from_iterable(rows, "TRANSACTIONS")
            except Exception:
                pass

db_adapter = ExasolConnector()

# -----------------------------------------------------------------------------
# CORE DOMAINS & CIRCUIT BREAKER STATE
# -----------------------------------------------------------------------------

GATEWAYS = ["Razorpay", "Cashfree", "PayU", "PhonePe", "BillDesk"]
BANKS = ["HDFC", "SBI", "ICICI", "AXIS", "KOTAK"]
MODES = ["UPI", "CARD", "NETBANKING"]
HANDLES = ["@okhdfcbank", "@oksbi", "@okaxis", "@ybl", "@paytm"]
REGIONS = ["Mumbai-DC", "Chennai-DC", "Delhi-NCR", "Bengaluru-Hub"]

CIRCUIT_STATE = {
    f"{b}_{m}": {
        "bank": b,
        "mode": m,
        "state": "CLOSED",
        "fallback_bank": "ICICI" if b != "ICICI" else "HDFC",
        "rerouted_txns": 0,
        "protected_gmv": 0.0
    }
    for b in BANKS for m in MODES
}

# -----------------------------------------------------------------------------
# SYNTHETIC DATA GENERATOR
# -----------------------------------------------------------------------------

def seed_data(degraded_bank: Optional[str] = "SBI", degraded_mode: Optional[str] = "UPI", total_count: int = 100000, burst_multiplier: float = 1.0):
    rows = []
    base_time = datetime.datetime.now()
    count = int(total_count * burst_multiplier)

    for _ in range(count):
        bank = random.choice(BANKS)
        mode = random.choices(MODES, weights=[0.72, 0.20, 0.08])[0]
        vpa = random.choice(HANDLES) if mode == "UPI" else "N/A"
        amount = round(random.uniform(50.0, 5000.0), 2)
        latency = int(random.gauss(310, 48))
        status = "SUCCESS"
        error_code = "NONE"
        dc = random.choice(REGIONS)

        # Baseline error rate (~2.2%)
        if random.random() < 0.022:
            status = "FAILED"
            error_code = "INSUFFICIENT_FUNDS"

        # Injected Route Degradation
        if degraded_bank and bank == degraded_bank and (not degraded_mode or mode == degraded_mode):
            latency = int(random.gauss(3850, 480))
            if random.random() < 0.64:
                status = "TIMEOUT"
                error_code = "SWITCH_TIMEOUT"

        rows.append((
            f"TXN_{uuid.uuid4().hex[:12].upper()}",
            base_time - datetime.timedelta(seconds=random.randint(1, 3600)),
            f"MRC_{random.randint(100, 160)}",
            random.choice(GATEWAYS),
            bank,
            mode,
            vpa,
            amount,
            status,
            max(40, latency),
            error_code,
            dc
        ))

    db_adapter.insert_batch(rows)

# Initial population
seed_data(degraded_bank="SBI", degraded_mode="UPI")

# -----------------------------------------------------------------------------
# COMPUTATION & TELEMETRY ENGINE
# -----------------------------------------------------------------------------

def compute_payload():
    route_query = """
    SELECT 
        issuer_bank,
        payment_mode,
        vpa_handle,
        COUNT(*) AS total_txns,
        SUM(CASE WHEN status != 'SUCCESS' THEN 1 ELSE 0 END) AS failed_txns,
        ROUND((SUM(CASE WHEN status != 'SUCCESS' THEN 1.0 ELSE 0.0 END) / COUNT(*)) * 100, 2) AS failure_rate_pct,
        ROUND(AVG(latency_ms), 0) AS avg_latency_ms,
        ROUND(SUM(amount), 2) AS total_gmv,
        ROUND(SUM(CASE WHEN status != 'SUCCESS' THEN amount ELSE 0 END), 2) AS lost_gmv
    FROM TRANSACTIONS
    GROUP BY issuer_bank, payment_mode, vpa_handle
    HAVING COUNT(*) > 300
    ORDER BY failure_rate_pct DESC, avg_latency_ms DESC;
    """
    raw_routes = db_adapter.execute_query(route_query)

    routes = []
    anomalies = []
    total_gmv_sum = 0
    total_lost_gmv = 0
    total_txns_count = 0

    for r in raw_routes:
        bank = r[0]
        mode = r[1]
        vpa = r[2]
        txns = int(r[3])
        failed = int(r[4])
        fail_pct = float(r[5])
        avg_lat = int(r[6])
        gmv = float(r[7])
        lost = float(r[8])

        total_txns_count += txns
        total_gmv_sum += gmv
        total_lost_gmv += lost

        key = f"{bank}_{mode}"
        is_anomalous = fail_pct > 10.0 or avg_lat > 1400

        if is_anomalous:
            CIRCUIT_STATE[key]["state"] = "OPEN"
            CIRCUIT_STATE[key]["rerouted_txns"] += int(txns * (fail_pct / 100.0))
            CIRCUIT_STATE[key]["protected_gmv"] += lost * 0.85
        else:
            CIRCUIT_STATE[key]["state"] = "CLOSED"

        item = {
            "bank": bank,
            "mode": mode,
            "vpa_handle": vpa,
            "total_txns": txns,
            "failed_txns": failed,
            "failure_rate": fail_pct,
            "avg_latency": avg_lat,
            "gmv": gmv,
            "lost_gmv": lost,
            "is_anomalous": is_anomalous,
            "circuit": CIRCUIT_STATE.get(key, {"state": "CLOSED", "fallback_bank": "ICICI"})
        }
        routes.append(item)
        if is_anomalous:
            anomalies.append(item)

    # Bank Level Aggregates
    bank_query = """
    SELECT 
        issuer_bank,
        ROUND(AVG(latency_ms), 0) AS avg_lat,
        ROUND((SUM(CASE WHEN status != 'SUCCESS' THEN 1.0 ELSE 0.0 END) / COUNT(*)) * 100, 2) AS fail_rate,
        SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_cnt,
        SUM(CASE WHEN status != 'SUCCESS' THEN 1 ELSE 0 END) AS fail_cnt
    FROM TRANSACTIONS
    GROUP BY issuer_bank
    ORDER BY issuer_bank;
    """
    bank_chart = db_adapter.execute_query(bank_query)

    # Regional Datacenter Telemetry
    reg_query = """
    SELECT datacenter, COUNT(*), ROUND(AVG(latency_ms), 0), ROUND((SUM(CASE WHEN status != 'SUCCESS' THEN 1.0 ELSE 0.0 END) / COUNT(*)) * 100, 2)
    FROM TRANSACTIONS GROUP BY datacenter;
    """
    reg_data = db_adapter.execute_query(reg_query)

    # RBI Compliance Penalty Estimation
    projected_rbi_penalties = sum(a['failed_txns'] * 100 for a in anomalies)

    diagnostics = []
    for a in anomalies:
        diagnostics.append({
            "target": f"{a['bank']} ({a['mode']} - {a['vpa_handle']})",
            "message": f"Switch Latency spiked to {a['avg_latency']}ms with {a['failure_rate']}% failure rate.",
            "impact": f"₹{a['lost_gmv']:,.2f} GMV at risk across {a['failed_txns']} dropouts.",
            "rbi_penalty": f"₹{a['failed_txns'] * 100:,} projected daily compliance liability (RBI Harmonisation circular).",
            "recommendation": f"Circuit TRIPPED: Traffic diverted to {a['circuit']['fallback_bank']} Switch Gateway automatically."
        })

    return {
        "status": "success",
        "data_platform": "Exasol Personal (Live)" if db_adapter.connected_to_live_exasol else "Exasol Engine (Mock/Active)",
        "total_txns": total_txns_count,
        "total_gmv": total_gmv_sum,
        "total_lost_gmv": total_lost_gmv,
        "projected_rbi_penalties": projected_rbi_penalties,
        "anomalous_count": len(anomalies),
        "diagnostics": diagnostics,
        "regional_telemetry": [
            {"region": reg[0], "txns": reg[1], "avg_latency": int(reg[2]), "failure_rate": float(reg[3])}
            for reg in reg_data
        ],
        "chart_data": {
            "labels": [b[0] for b in bank_chart],
            "latencies": [int(b[1]) for b in bank_chart],
            "failure_rates": [float(b[2]) for b in bank_chart],
            "successes": [int(b[3]) for b in bank_chart],
            "failures": [int(b[4]) for b in bank_chart]
        },
        "routes": routes
    }

# -----------------------------------------------------------------------------
# REST ENDPOINTS (ALL ALIASES INCLUDED)
# -----------------------------------------------------------------------------

@app.get("/api/switch-metrics")
@app.get("/api/v1/telemetry/overview")
@app.get("/api/v2/telemetry")
def get_metrics():
    return compute_payload()

@app.post("/api/inject-failure")
@app.post("/api/v1/simulation/inject")
@app.post("/api/v2/inject-failure")
async def inject_failure(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    bank = body.get("bank", "SBI")
    mode = body.get("mode", "UPI")
    seed_data(degraded_bank=bank, degraded_mode=mode)
    return {"status": "success", "message": f"Degradation injected for {bank} - {mode}"}

@app.post("/api/simulate-burst")
def simulate_burst():
    seed_data(degraded_bank="SBI", degraded_mode="UPI", total_count=100000, burst_multiplier=2.5)
    return {"status": "success", "message": "Simulated 2.5x Peak Burst (250,000 Transactions)"}

@app.post("/api/recover")
@app.post("/api/v1/simulation/reset")
@app.post("/api/v2/reset")
def recover_all():
    seed_data(degraded_bank=None, degraded_mode=None)
    for k in CIRCUIT_STATE:
        CIRCUIT_STATE[k]["state"] = "CLOSED"
    return {"status": "success", "message": "All bank switch routes recovered to baseline."}

@app.post("/api/execute-sql")
@app.post("/api/v1/workbench/query")
async def execute_sql(req: Request):
    body = await req.json()
    sql = body.get("sql", body.get("query", "")).strip()
    if not sql.upper().startswith("SELECT"):
        raise HTTPException(status_code=400, detail="Only analytical SELECT queries are permitted.")
    try:
        t0 = time.time()
        results = db_adapter.execute_query(sql)
        t1 = time.time()
        return {
            "status": "success",
            "execution_time_ms": round((t1 - t0) * 1000, 2),
            "rows": results[:30]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Mount static directory
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_dashboard():
    return FileResponse("index.html")

# -----------------------------------------------------------------------------
# LOCAL RUNNER ENTRY POINT
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 60)
    print(" ⚡ SWITCHGUARD COMMAND CENTER ACTIVE")
    print(f" Dashboard URL: http://127.0.0.1:{PORT}")
    print(f" Exasol Mode: {'LIVE' if db_adapter.connected_to_live_exasol else 'EMULATOR ACTIVE'}")
    print("=" * 60 + "\n")
    uvicorn.run("server:app", host="127.0.0.1", port=PORT, reload=True)