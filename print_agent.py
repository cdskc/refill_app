"""
Print Agent — runs at each pharmacy location.

Polls the API server for pending refill requests for this store,
then prints a label to the local Zebra GK420d via raw TCP socket.

Usage:
    python print_agent.py --store 157 --server https://refills.cdskc.me

    The printer IP is fetched automatically from the server based on the store ID.
    Override it locally (e.g. for testing) with --printer:
    python print_agent.py --store 157 --server https://refills.cdskc.me --printer 192.168.1.50

Configuration can also be set via environment variables:
    STORE_ID=157
    SERVER_URL=https://refills.cdskc.me
    PRINTER_IP=192.168.1.50   # optional override
    PRINTER_PORT=9100
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

def generate_zpl_label(rx_number: str, store_id: str,
                       patient_name: str = "", created_at: str = "") -> str:
    """
    Generate a ZPL label for a refill request.

    Label stock: Cosentino's Pharmacy labels — 2" tall x 3.25" wide.
    The printer feeds the 2" edge first, so:
      ^PW = 406 dots  (2.00" — the print-head width)
      ^LL = 659 dots  (3.25" — the feed/label length)

    Pre-printed elements (do not overprint):
      - Top-left: Cosentino's PHARMACY logo
      - Bottom: FDA side-effects notice

    Because the physical label is landscape but the printer feeds
    portrait, we use ^FWR (Field Default: Rotate 90° clockwise)
    to rotate all content.  In rotated mode the coordinate system is:
      ^FO x,y  where:
        x = distance from LEFT edge of label  (0–406, the 2" axis)
        y = distance from TOP edge of label   (0–659, the 3.25" axis)
    Text/barcodes render rotated so they read correctly when the
    label is viewed in landscape orientation.

    ZPL units: 203 dpi  =>  1 inch = 203 dots.
    """
    # Parse and format the timestamp
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        time_str = dt.astimezone().strftime("%m/%d/%Y %I:%M %p")
    except Exception:
        time_str = datetime.now().strftime("%m/%d/%Y %I:%M %p")

    patient_line = f"Name: {patient_name}" if patient_name else ""

    # Layout reference — visual positions on the label as seen landscape.
    # The printer feeds the 2" edge (print-head width = 406 dots).
    # The 3.25" dimension is the label length (659 dots, feed direction).
    #
    # With ^FWR all fields are rotated 90° CW.  The X coordinate in
    # ^FO controls the vertical position on the physical label, but it
    # counts from the BOTTOM of the label upward.  To get an intuitive
    # top-down layout we compute:
    #       zpl_x = 406 - visual_y - element_height
    #
    # Visual layout (top of label = logo):
    #   visual_y  0– 65 : pre-printed logo — avoid
    #   visual_y  70     : "*** REFILL REQUEST ***"  (34 tall)
    #   visual_y 110     : Rx# (50 tall)
    #   visual_y 166     : Patient name (24 tall)
    #   visual_y 196     : Store + Submitted (20 tall)
    #   visual_y 224     : separator line (2 tall)
    #   visual_y 232     : "Please pull and process." (22 tall)
    #   visual_y 262     : barcode (50 tall)
    #   visual_y 380–406 : pre-printed FDA notice — avoid

    def _vy(visual_y, height):
        """Convert visual Y (top-down) to ZPL ^FO X (bottom-up)."""
        return 406 - visual_y - height

    zpl = f"""
^XA
^PW406
^LL659
^CF0,20
^FWR

~SD25

^FO{_vy(70, 34)},20^A0R,34,34^FD*** REFILL REQUEST ***^FS

^FO{_vy(110, 50)},20^A0R,50,50^FDRx# {rx_number}^FS

^FO{_vy(166, 24)},20^A0R,24,24^FD{patient_line}^FS

^FO{_vy(196, 20)},20^A0R,20,20^FDStore: {store_id}^FS
^FO{_vy(196, 20)},300^A0R,20,20^FDSubmitted: {time_str}^FS

^FO{_vy(224, 2)},20^GB2,620,2^FS
^FO{_vy(232, 22)},20^A0R,22,22^FDPlease pull and process.^FS

^FO{_vy(262, 50)},20^BY2,2,50^BCR,50,Y,N,N^FD{rx_number}^FS

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
# Server config fetch
# ---------------------------------------------------------------------------

def fetch_printer_config(server_url: str, store_id: str) -> tuple[str, int]:
    """Fetch printer IP and port from the server's store config endpoint."""
    try:
        resp = requests.get(
            f"{server_url.rstrip('/')}/api/store-config/{store_id}",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("printer_ip", ""), data.get("printer_port", 9100)
    except Exception as e:
        print(f"  [WARN] Could not fetch printer config from server: {e}")
        return "", 9100


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

    printer_ip = args.printer
    printer_port = args.printer_port

    if not printer_ip:
        print("No --printer specified — fetching printer config from server...")
        printer_ip, printer_port = fetch_printer_config(args.server, args.store)
        if printer_ip:
            print(f"  Using printer {printer_ip}:{printer_port} (from server config)")
        else:
            print("WARNING: No printer IP configured for this store. Running in console mode.")
            print("         ZPL will be printed to stdout instead of a Zebra printer.")
        print()

    poll_and_print(
        server_url=args.server,
        store_id=args.store,
        printer_ip=printer_ip,
        printer_port=printer_port,
        poll_interval=args.interval,
    )


if __name__ == "__main__":
    main()
