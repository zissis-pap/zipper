"""
builder.py – Reconstruct the target file from a reference and a patch.

Usage
-----
  python builder.py <reference.bin> <patch.bin> [output.bin]

  reference.bin : the file passed as <file1> to zipper.py
  patch.bin     : the patch produced by zipper.py
  output.bin    : (optional) output path; defaults to rebuilt.bin

Steps
-----
  1. Read the binary patch file and extract the raw patch bytes and CRC-32.
  2. Read the 1-byte patch_size header from the raw patch bytes.
  3. Parse the record stream and apply each record against reference.bin
     to reconstruct the target file.
  4. Validate the reconstructed file against the stored CRC-32.
"""

import os
import struct
import sys
import zlib

from zipper import rle_decode


# ── binary .bin → raw patch bytes ─────────────────────────────────────────────

def decode_bin(path: str) -> tuple[bytes, int]:
    """
    Read a binary patch file and return (raw_patch_bytes, crc32_of_file2).

    File layout:
      [raw patch bytes][4-byte CRC-32 BE of the target file (file2)]

    CRC validation is deferred to the caller, after the target file is
    reconstructed via apply_patch().
    """
    with open(path, "rb") as fh:
        raw = fh.read()

    if len(raw) < 5:
        raise ValueError("Binary patch file is too short.")

    data = raw[:-4]
    crc_stored = struct.unpack(">I", raw[-4:])[0]

    return bytes(data), crc_stored


# ── binary patch → reconstructed file ─────────────────────────────────────────

def apply_patch(patch_bin: bytes, reference: bytes) -> bytes:
    """
    Parse the binary patch and reconstruct the target file.

    Binary patch layout:
      Byte 0    : patch_size (uint8)
      Bytes 1+  : record stream

    Records:
      0x43                    – copy reference[out_pos : out_pos + patch_size]
      0x44  count[1]          – run of (count+1) consecutive same-position matches
      0x52  offset[2:0] BE   – copy reference[offset : offset + patch_size]
      0x49  data[patch_size] – emit raw bytes from the patch
      0x58  len[1] rle[len]  – XOR delta + RLE against reference at out_pos
      0x50  len[1] data[len] – trailing partial block

    Trailing 0x00 bytes are treated as chunk-padding and silently ignored.
    """
    if len(patch_bin) < 1:
        raise ValueError("Binary patch is too short to contain the patch_size header.")

    patch_size = patch_bin[0]
    if patch_size == 0 or patch_size % 8 != 0:
        raise ValueError(
            f"Invalid patch_size in header: {patch_size} "
            f"(must be a positive multiple of 8)."
        )

    # Zero-pad reference so any block read stays in-bounds
    ref = reference + b"\x00" * patch_size

    out     = bytearray()
    out_pos = 0           # current write position (advances by patch_size each record)
    pos     = 1           # current read position in patch_bin

    while pos < len(patch_bin):
        rec = patch_bin[pos]

        if rec == 0x00:
            break         # trailing chunk-padding; we're done

        elif rec == 0x43:
            out    += ref[out_pos : out_pos + patch_size]
            out_pos += patch_size
            pos     += 1

        elif rec == 0x44:
            if pos + 2 > len(patch_bin):
                raise ValueError(f"Truncated 0x44 record at patch offset {pos}.")
            count = patch_bin[pos + 1] + 1
            for _ in range(count):
                out    += ref[out_pos : out_pos + patch_size]
                out_pos += patch_size
            pos += 2

        elif rec == 0x52:
            if pos + 4 > len(patch_bin):
                raise ValueError(f"Truncated 0x52 record at patch offset {pos}.")
            offset  = int.from_bytes(patch_bin[pos + 1 : pos + 4], "big")
            out    += ref[offset : offset + patch_size]
            out_pos += patch_size
            pos     += 4

        elif rec == 0x49:
            end = pos + 1 + patch_size
            if end > len(patch_bin):
                raise ValueError(f"Truncated 0x49 record at patch offset {pos}.")
            out    += patch_bin[pos + 1 : end]
            out_pos += patch_size
            pos     = end

        elif rec == 0x58:
            if pos + 2 > len(patch_bin):
                raise ValueError(f"Truncated 0x58 record at patch offset {pos}.")
            rle_len = patch_bin[pos + 1]
            end = pos + 2 + rle_len
            if end > len(patch_bin):
                raise ValueError(f"Truncated 0x58 record at patch offset {pos}.")
            xor_delta = rle_decode(patch_bin[pos + 2 : end])
            ref_block = ref[out_pos : out_pos + patch_size]
            out    += bytes(a ^ b for a, b in zip(xor_delta, ref_block))
            out_pos += patch_size
            pos     = end

        elif rec == 0x50:
            if pos + 2 > len(patch_bin):
                raise ValueError(f"Truncated 0x50 record at patch offset {pos}.")
            length = patch_bin[pos + 1]
            end = pos + 2 + length
            if end > len(patch_bin):
                raise ValueError(f"Truncated 0x50 record at patch offset {pos}.")
            out += patch_bin[pos + 2 : end]
            pos  = end

        else:
            raise ValueError(
                f"Unknown record type 0x{rec:02X} at patch offset {pos}."
            )

    return bytes(out)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) not in (3, 4):
        sys.exit("Usage: python builder.py <reference.bin> <patch.bin> [output.bin]")

    ref_path   = sys.argv[1]
    patch_path = sys.argv[2]
    out_path   = sys.argv[3] if len(sys.argv) == 4 else None

    for p in (ref_path, patch_path):
        if not os.path.isfile(p):
            sys.exit(f"Error: '{p}' not found.")

    if out_path is None:
        out_path = "rebuilt.bin"

    print(f"Reference : {ref_path}  ({os.path.getsize(ref_path):,} bytes)")
    print(f"Patch     : {patch_path}")
    print(f"Output    : {out_path}")
    print()

    ext = os.path.splitext(patch_path)[1].lower()
    if ext != ".bin":
        sys.exit(f"Unrecognised patch extension '{ext}'. Expected .bin")

    print("Decoding patch …")
    try:
        patch_data, crc_expected = decode_bin(patch_path)
    except ValueError as exc:
        sys.exit(f"Decode error: {exc}")

    patch_size = patch_data[0]
    print(f"  Raw patch : {len(patch_data):,} bytes  |  patch_size = {patch_size}")

    with open(ref_path, "rb") as fh:
        reference = fh.read()

    print("Applying patch …")
    try:
        result = apply_patch(patch_data, reference)
    except ValueError as exc:
        sys.exit(f"Patch error: {exc}")

    crc_calc = zlib.crc32(result) & 0xFFFF_FFFF
    if crc_expected != crc_calc:
        sys.exit(
            f"CRC-32 mismatch: expected {crc_expected:#010x}, "
            f"reconstructed file has {crc_calc:#010x}."
        )
    print(f"  CRC-32 OK ({crc_calc:#010x})")

    with open(out_path, "wb") as fh:
        fh.write(result)

    print(f"\nDone.  Reconstructed : {out_path}  ({len(result):,} bytes)")


if __name__ == "__main__":
    main()
