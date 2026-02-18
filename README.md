# Prescription Refill Request — Proof of Concept

A lightweight system for patients to submit prescription refill requests via a mobile-friendly web form, with requests printed as labels at the destination pharmacy.

## Architecture

```
Patient's Phone (web form)
        │
        ▼
   API Server (FastAPI + SQLite)
        │
        ▼
   Print Agent (per store)
        │
        ▼
   Zebra GK420d (ZPL label)
```

## Components

| File | Purpose |
|------|---------|
| `server.py` | FastAPI app — serves the web form, receives requests, stores in SQLite |
| `index.html` | Patient-facing mobile web form |
| `stores.py` | Store directory (locations, printer IPs, hours) |
| `print_agent.py` | Runs at each store — polls for requests, prints ZPL labels |
| `requirements.txt` | Python dependencies |

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start the API server

```bash
python server.py
# or
uvicorn server:app --host 0.0.0.0 --port 8000
```

The web form is now available at `http://localhost:8000`.

### 3. Start a print agent (one per store)

```bash
# Console mode (no printer — prints ZPL to terminal):
python print_agent.py --store 157 --server http://your-server:8000

# With Zebra printer:
python print_agent.py --store 157 --printer 192.168.1.50 --server http://your-server:8000
```

Or use environment variables:
```bash
export STORE_ID=157
export PRINTER_IP=192.168.1.50
export SERVER_URL=http://your-server:8000
python print_agent.py
```

## Configuration

### Store locations
Edit `stores.py` to update store details. Each store entry includes:
- Name, address, phone, hours
- `printer_ip` — set to the Zebra GK420d's IP address on the local network
- `printer_port` — default 9100 (standard ZPL raw printing port)

### Printer setup
The Zebra GK420d should be configured for:
- **Connection:** Ethernet, with a static IP on the pharmacy LAN
- **Port:** 9100 (default raw TCP port)
- **Label size:** ~2" x 3" (adjustable in `print_agent.py`'s `generate_zpl_label()`)

To find the printer's current IP, print a configuration label by holding the
feed button while powering on the printer.

### ZPL label adjustment
The label layout in `print_agent.py` targets a 2" x 3" printable area at 203 dpi.
Key ZPL parameters to adjust:
- `^PW464` — print width in dots (464 ≈ 2.3")
- `^LL609` — label length in dots (609 ≈ 3")
- `~SD25` — print darkness (1-30, higher = darker)

## Deployment Options

### Option A: Self-hosted on pharmacy network
Run the server on a machine at one of the stores. Use Cloudflare Tunnel
to expose the web form to the internet without port forwarding:

```bash
# Install cloudflared
# Create tunnel (one-time):
cloudflared tunnel create refill-app
cloudflared tunnel route dns refill-app refills.yourpharmacy.com

# Run:
cloudflared tunnel run --url http://localhost:8000 refill-app
```

### Option B: Small cloud VPS
Host the server on a $5-6/mo VPS (DigitalOcean, Linode, Vultr).
Print agents at each store connect outbound to the server.

## Barcode Reference (EnterpriseRx)

The Code 128 barcode on prescription labels encodes:
```
6876386  01  157
├──────┘ ├─┘ ├─┘
Rx#      Fill Store
(7 dig)  (2)  (3)
```

This will be useful for a future barcode scanning feature in the mobile app.

## Security Notes

- **No PHI is stored or transmitted.** The system only handles Rx numbers (which are
  not PHI by themselves), optional first names, and store IDs.
- The SQLite database stores only request metadata.
- For production: add HTTPS (via Cloudflare Tunnel or a reverse proxy), rate limiting,
  and consider a simple CAPTCHA to prevent abuse.
- The `/api/pending/{store_id}` endpoint should be protected with an API key in
  production to prevent unauthorized access to the request queue.
