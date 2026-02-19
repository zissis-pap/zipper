"""
main.py – Binary file patch generator (with XOR-delta + RLE compression)

Usage
-----
  python -m zipper.main <file1.bin> <file2.bin>

The script compares file1.bin against file2.bin in patch_size-byte blocks
and produces:
  <stem>_patch.bin  – compact binary patch stream
  <stem>_patch.txt  – same data chunked into CRC-verified hex lines

See zipper.py and encoder.py for format details.
"""

import math
import os
import sys

from .encoder import encode_to_bin
from .zipper import generate_patch


# ── Interactive prompts ────────────────────────────────────────────────────────

def _ask_patch_size() -> int:
    """Ask for patch_size; must be a positive multiple of 8. Default: 64."""
    while True:
        raw = input("Patch size in bytes (multiple of 8) [64]: ").strip()
        if not raw:
            return 64
        try:
            val = int(raw)
        except ValueError:
            print("  Please enter a whole number.")
            continue
        if val <= 0 or val % 8 != 0:
            print("  Must be a positive multiple of 8.")
        else:
            return val



# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("Usage: python -m zipper.main <file1.bin> <file2.bin>")

    path1, path2 = sys.argv[1], sys.argv[2]

    for p in (path1, path2):
        if not os.path.isfile(p):
            sys.exit(f"Error: '{p}' not found.")

    print(f"File 1 : {path1}  ({os.path.getsize(path1):,} bytes)")
    print(f"File 2 : {path2}  ({os.path.getsize(path2):,} bytes)")
    print()

    patch_size = _ask_patch_size()

    with open(path1, "rb") as fh:
        data1 = fh.read()
    with open(path2, "rb") as fh:
        data2 = fh.read()

    print(f"\nBuilding patch  (patch_size={patch_size} B) …")

    patch = generate_patch(data2, data1, patch_size)

    # Derive output paths from file1's stem
    stem    = os.path.splitext(path1)[0]
    bin_out = stem + "_patch.bin"
    txt_out = stem + "_patch.txt"

    encode_to_bin(patch, bin_out, data2)

    bin_size   = os.path.getsize(bin_out)
    ratio      = (1 - bin_size / len(data2)) * 100

    print(f"\nDone.")
    print(f"  Patch binary : {bin_out}  ({bin_size:,} bytes)")
    print(
        f"  Patch text   : {txt_out}  "
    )
    print(
        f"  Compression  : {bin_size:,} B patch vs {len(data2):,} B {path2}"
        f"  →  {ratio:.1f}%"
    )


if __name__ == "__main__":
    main()
