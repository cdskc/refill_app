"""
Print Agent — runs at each pharmacy location.

Polls the API server for pending refill requests for this store,
then prints a label to the local Zebra GK420d via raw TCP socket.

Usage:
    python print_agent.py --store 157 --printer 192.168.1.50 --server http://your-server:8000

Configuration can also be set via environment variables:
    STORE_ID=157
    PRINTER_IP=192.168.1.50
    PRINTER_PORT=9100
    SERVER_URL=http://your-server:8000
    POLL_INTERVAL=5
"""

import argparse
import os
import socket
import sys
import time
from datetime import datetime, timezone

import requests


# ---------------------------------------------------------------------------
# ZPL label generation
# ---------------------------------------------------------------------------

def generate_zpl_label(rx_number: str, store_id: str, request_id: str,
                       patient_name: str = "", created_at: str = "") -> str:
    """
    Generate a ZPL label for a refill request.

    Targets approximately 2" x 3" printable area on the Zebra GK420d.
    ZPL units: 203 dpi, so 1 inch = 203 dots.
      2" wide  = ~406 dots
      3" tall  = ~609 dots

    Adjust ^PW (print width) and ^LL (label length) to match your actual
    label stock. These values are a reasonable starting point.
    """
    # Parse and format the timestamp
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        time_str = dt.astimezone().strftime("%m/%d/%Y %I:%M %p")
    except Exception:
        time_str = datetime.now().strftime("%m/%d/%Y %I:%M %p")

    patient_line = f"Name: {patient_name}" if patient_name else ""

    zpl = f"""
^XA
^PW464
^LL609
^CF0,20

~SD25

^FO20,20^A0N,40,40^FD*** REFILL REQUEST ***^FS

^FO20,80^A0N,55,55^FDRx# {rx_number}^FS

^FO20,160^A0N,28,28^FDStore: {store_id}^FS
^FO20,200^A0N,28,28^FD{patient_line}^FS

^FO20,260^A0N,22,22^FDSubmitted: {time_str}^FS
^FO20,295^A0N,22,22^FDRef: {request_id}^FS

^FO20,345^GB420,0,2^FS
^FO20,365^A0N,24,24^FDPlease pull and process.^FS

^FO20,420^BY2,2,60^BCN,60,Y,N,N^FD{rx_number}^FS

^XZ
""".strip()
    return zpl


# ---------------------------------------------------------------------------
# Printer communication
# ---------------------------------------------------------------------------

def send_to_printer(zpl: str, printer_ip: str, printer_port: int = 9100,
                    timeout: float = 5.0) -> bool:
    """Send raw ZPL to the Zebra printer via TCP socket."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((printer_ip, printer_port))
            sock.sendall(zpl.encode("utf-8"))
        return True
    except (socket.error, OSError) as e:
        print(f"  [ERROR] Printer connection failed: {e}")
        return False


def print_to_console(zpl: str):
    """Fallback: dump ZPL to console for testing without a printer."""
    print("  --- ZPL OUTPUT (no printer configured) ---")
    print(zpl)
    print("  --- END ZPL ---")


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def poll_and_print(server_url: str, store_id: str, printer_ip: str,
                   printer_port: int, poll_interval: int):
    """Main loop: poll server, print labels."""
    endpoint = f"{server_url.rstrip('/')}/api/pending/{store_id}"

    print(f"Print Agent started")
    print(f"  Store:    {store_id}")
    print(f"  Server:   {server_url}")
    print(f"  Printer:  {printer_ip or '(console mode)'}:{printer_port}")
    print(f"  Polling every {poll_interval}s")
    print()

    while True:
        try:
            resp = requests.get(endpoint, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            for req in data.get("requests", []):
                rid = req["id"]
                rx = req["rx_number"]
                name = req.get("patient_name", "")
                created = req.get("created_at", "")

                print(f"  >> New refill: Rx# {rx} (ref: {rid})")

                zpl = generate_zpl_label(
                    rx_number=rx,
                    store_id=store_id,
                    request_id=rid,
                    patient_name=name,
                    created_at=created,
                )

                if printer_ip:
                    success = send_to_printer(zpl, printer_ip, printer_port)
                else:
                    print_to_console(zpl)
                    success = True

                # Report back to server
                try:
                    if success:
                        requests.post(
                            f"{server_url.rstrip('/')}/api/printed/{rid}",
                            timeout=5,
                        )
                        print(f"     Printed successfully.")
                    else:
                        requests.post(
                            f"{server_url.rstrip('/')}/api/print-error/{rid}",
                            timeout=5,
                        )
                        print(f"     Print failed — will retry.")
                except Exception as e:
                    print(f"     Warning: could not report status: {e}")

        except requests.ConnectionError:
            print(f"  [WARN] Cannot reach server at {server_url} — retrying...")
        except Exception as e:
            print(f"  [ERROR] {e}")

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Refill Request Print Agent")
    parser.add_argument("--store", default=os.environ.get("STORE_ID", ""),
                        help="Store ID (e.g. 157)")
    parser.add_argument("--printer", default=os.environ.get("PRINTER_IP", ""),
                        help="Zebra printer IP address")
    parser.add_argument("--printer-port", type=int,
                        default=int(os.environ.get("PRINTER_PORT", "9100")),
                        help="Printer port (default: 9100)")
    parser.add_argument("--server", default=os.environ.get("SERVER_URL", "http://localhost:8000"),
                        help="API server URL")
    parser.add_argument("--interval", type=int,
                        default=int(os.environ.get("POLL_INTERVAL", "5")),
                        help="Poll interval in seconds (default: 5)")

    args = parser.parse_args()

    if not args.store:
        print("Error: --store is required (or set STORE_ID env var)")
        sys.exit(1)

    if not args.printer:
        print("WARNING: No --printer specified. Running in console mode.")
        print("         ZPL will be printed to stdout instead of a Zebra printer.")
        print()

    poll_and_print(
        server_url=args.server,
        store_id=args.store,
        printer_ip=args.printer,
        printer_port=args.printer_port,
        poll_interval=args.interval,
    )


if __name__ == "__main__":
    main()
