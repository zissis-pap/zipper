"""
encoder.py – Encode the binary patch stream into a .bin file
             with a single whole-file CRC-32.

  .bin layout:
    [raw patch bytes][4-byte CRC-32 BE of the target file (file2)]
"""

import struct
import zlib


def encode_to_bin(data: bytes, out_path: str, file2: bytes) -> None:
    """Write raw patch bytes followed by a 4-byte CRC-32 of the target file (file2)."""
    crc = zlib.crc32(file2) & 0xFFFF_FFFF
    with open(out_path, "wb") as fh:
        fh.write(data)
        fh.write(struct.pack(">I", crc))

