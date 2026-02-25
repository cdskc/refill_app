#!/usr/bin/env python3
"""
Print QR code stickers for prescription bottle lids.

Prints small QR stickers linking to refills.cdskc.me on the same
Cosentino's label stock (2" x 3.25"). Each sticker has "SCAN" above
and "TO REFILL" below the QR code, with a 30mm circular cut guide
sized to fit a 35mm bottle lid.

Usage:
    python print_qr_stickers.py --count 5 --printer 192.168.1.50
    python print_qr_stickers.py                # preview ZPL only
"""

import argparse
import socket
import sys


URL = "https://refills.cdskc.me"

# Label stock: 2" x 3.25" at 203 dpi
LABEL_WIDTH = 406   # 2" (print-head width)
LABEL_LENGTH = 659  # 3.25" (feed direction)

# 30mm circle fits comfortably on a 35mm bottle cap
STICKER_DOTS = 243  # 30mm at 203 dpi


def generate_qr_sticker_zpl() -> str:
    """Generate ZPL for one QR code sticker centered on the label.

    No rotation — the sticker is a cut-out circle so orientation doesn't matter.
    Label is 406 wide × 659 tall in the printer's native (portrait) coordinate
    system: x across the 2" print head, y along the 3.25" feed direction.

    Layout (top to bottom):
      30mm circle cut guide centered on label
      "SCAN" centered above QR code
      QR code (mag 5, ~15.6mm) encoding refills.cdskc.me
      "TO REFILL" centered below QR code

    Positions hand-tuned in Labelary for visual centering.
    """
    return f"""^XA
^PW{LABEL_WIDTH}
^LL{LABEL_LENGTH}
~SD25
^FO81,208^GE243,243,2^FS
^FO84,228^FB243,1,0,C^A0N,24,18^FDSCAN^FS
^FO142,250^BQN,2,5^FDMA,{URL}^FS
^FO84,402^FB243,1,0,C^A0N,24,18^FDTO REFILL^FS
^BY2,2,50
^XZ"""


def send_to_printer(zpl: str, ip: str, port: int = 9100) -> bool:
    """Send raw ZPL to Zebra printer via TCP."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5.0)
            s.connect((ip, port))
            s.sendall(zpl.encode("utf-8"))
        return True
    except (socket.error, OSError) as e:
        print(f"  [ERROR] {e}")
        return False


def main():
    p = argparse.ArgumentParser(description="Print QR stickers for bottle lids")
    p.add_argument("--count", type=int, default=1, help="Number of stickers")
    p.add_argument("--printer", default="", help="Zebra printer IP")
    p.add_argument("--port", type=int, default=9100)
    args = p.parse_args()

    zpl = generate_qr_sticker_zpl()

    if not args.printer:
        print("No --printer specified. ZPL preview:\n")
        print(zpl)
        return

    print(f"Printing {args.count} sticker(s) to {args.printer}:{args.port}...")
    for i in range(args.count):
        if send_to_printer(zpl, args.printer, args.port):
            print(f"  [{i + 1}/{args.count}] OK")
        else:
            print(f"  [{i + 1}/{args.count}] FAILED")
            sys.exit(1)
    print("Done!")


if __name__ == "__main__":
    main()
