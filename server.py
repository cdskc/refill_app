"""
Refill Request API Server

Receives refill requests from the patient web form, validates them,
stores them in a simple SQLite database, and makes them available
for print agents to pick up.

Run with: uvicorn server:app --host 0.0.0.0 --port 8000
"""

import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator

from stores import STORES, get_store_list_for_form


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).parent / "refill_requests.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS refill_requests (
            id TEXT PRIMARY KEY,
            rx_number TEXT NOT NULL,
            store_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            printed_at TEXT,
            patient_name TEXT
        )
    """)
    conn.commit()
    conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Refill Request POC", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RefillRequest(BaseModel):
    rx_number: str
    store_id: str
    patient_name: str = ""  # Optional - just first name for the label

    @field_validator("rx_number")
    @classmethod
    def validate_rx_number(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit():
            raise ValueError("Prescription number must contain only digits")
        if len(v) != 7:
            raise ValueError("Prescription number must be exactly 7 digits")
        if v[0] not in "2468":
            raise ValueError("Prescription number must start with 2, 4, 6, or 8")
        return v

    @field_validator("store_id")
    @classmethod
    def validate_store_id(cls, v: str) -> str:
        v = v.strip()
        if v not in STORES:
            raise ValueError(f"Invalid store: {v}")
        return v


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def serve_form():
    """Serve the patient-facing web form."""
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/api/stores")
async def list_stores():
    """Return store list for the form dropdown."""
    return get_store_list_for_form()


@app.post("/api/refill")
async def submit_refill(req: RefillRequest):
    """Submit a new refill request."""
    request_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    conn = get_db()
    conn.execute(
        """INSERT INTO refill_requests (id, rx_number, store_id, status, created_at, patient_name)
           VALUES (?, ?, ?, 'pending', ?, ?)""",
        (request_id, req.rx_number, req.store_id, now, req.patient_name.strip()),
    )
    conn.commit()
    conn.close()

    store = STORES[req.store_id]
    return {
        "success": True,
        "request_id": request_id,
        "message": f"Refill request submitted to {store['name']} in {store['city']}.",
        "store_phone": store["phone"],
    }


@app.get("/api/pending/{store_id}")
async def get_pending(store_id: str):
    """
    Print agent endpoint: fetch pending requests for a store.
    Returns pending requests and marks them as 'printing'.
    """
    if store_id not in STORES:
        raise HTTPException(status_code=404, detail="Store not found")

    conn = get_db()
    rows = conn.execute(
        """SELECT id, rx_number, store_id, created_at, patient_name
           FROM refill_requests
           WHERE store_id = ? AND status = 'pending'
           ORDER BY created_at ASC""",
        (store_id,),
    ).fetchall()

    if not rows:
        conn.close()
        return {"requests": []}

    # Mark as printing
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" for _ in ids)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        f"""UPDATE refill_requests SET status = 'printing', printed_at = ?
            WHERE id IN ({placeholders})""",
        [now] + ids,
    )
    conn.commit()
    conn.close()

    return {
        "requests": [
            {
                "id": r["id"],
                "rx_number": r["rx_number"],
                "store_id": r["store_id"],
                "created_at": r["created_at"],
                "patient_name": r["patient_name"],
            }
            for r in rows
        ]
    }


@app.post("/api/printed/{request_id}")
async def mark_printed(request_id: str):
    """Print agent confirms a request was successfully printed."""
    conn = get_db()
    conn.execute(
        "UPDATE refill_requests SET status = 'printed' WHERE id = ?",
        (request_id,),
    )
    conn.commit()
    conn.close()
    return {"success": True}


@app.post("/api/print-error/{request_id}")
async def mark_print_error(request_id: str):
    """Print agent reports a print failure — reset to pending for retry."""
    conn = get_db()
    conn.execute(
        "UPDATE refill_requests SET status = 'pending', printed_at = NULL WHERE id = ?",
        (request_id,),
    )
    conn.commit()
    conn.close()
    return {"success": True}


# ---------------------------------------------------------------------------
# Run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
