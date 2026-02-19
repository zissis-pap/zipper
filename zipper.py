"""
zipper.py – Binary patch generation with XOR-delta + RLE compression.

Binary patch layout
-------------------
  Byte 0    : patch_size as uint8
  Bytes 1+  : record stream

Record types in the stream:
  0x43                      – file1 block matches file2 at the same byte offset  (1 byte)
  0x44  count[1]             – run of 2-256 consecutive 0x43 matches             (2 bytes, count+1 = actual)
  0x52  offset[2:0]         – file1 block found in file2 at a different offset   (4 bytes, BE)
  0x49  data[0:patch_size]  – file1 block not found in file2; raw data follows   (1 + patch_size bytes)
  0x58  len[1] rle[0:len]   – XOR delta + RLE against reference at same offset   (2 + len bytes)
  0x50  len[1] data[0:len]  – trailing partial block; raw data follows           (2 + len bytes)

0x58 is used instead of 0x49 when XOR + RLE produces a smaller encoding.

RLE format (used inside 0x58 records):
  Control byte with high bit clear (0x00-0x7F): literal run
    Low 7 bits + 1 = count (1-128). Followed by count literal bytes.
  Control byte with high bit set (0x80-0xFF): repeat run
    Low 7 bits + 2 = count (2-129). Followed by 1 byte to repeat.

Search priority for 0x52:
  1. Aligned positions in file2 (multiples of patch_size)
  2. All remaining (unaligned) positions in file2

The maximum representable 3-byte offset is 0xFFFFFF (≈ 16 MB).
"""

_MAX_OFFSET = 0xFF_FF_FF  # largest offset storable in 3 bytes


def _build_lookup(file2: bytes, patch_size: int) -> dict:
    """
    Return a dict mapping each patch_size-byte block found in file2 to its
    earliest byte offset.  Aligned offsets are inserted first so they win
    when the same block appears at both aligned and unaligned positions.
    """
    cap = min(len(file2), _MAX_OFFSET + 1)
    lookup: dict[bytes, int] = {}

    # Pass 1 – aligned positions (multiples of patch_size)
    for j in range(0, cap - patch_size + 1, patch_size):
        key = file2[j : j + patch_size]
        if key not in lookup:
            lookup[key] = j

    # Pass 2 – unaligned positions
    for j in range(0, cap - patch_size + 1):
        if j % patch_size != 0:
            key = file2[j : j + patch_size]
            if key not in lookup:
                lookup[key] = j

    return lookup


def rle_encode(data: bytes) -> bytes:
    """RLE-encode a byte sequence. See module docstring for format."""
    out = bytearray()
    i = 0
    n = len(data)

    while i < n:
        # Check for a run of identical bytes (need at least 2)
        run_byte = data[i]
        run_len = 1
        while i + run_len < n and data[i + run_len] == run_byte and run_len < 129:
            run_len += 1

        if run_len >= 2:
            # Emit repeat run
            out.append(0x80 | (run_len - 2))
            out.append(run_byte)
            i += run_len
        else:
            # Collect literal bytes (up to 128)
            lit_start = i
            lit_len = 1
            i += 1
            while lit_len < 128 and i < n:
                # Stop if next bytes form a run of 2+
                if i + 1 < n and data[i] == data[i + 1]:
                    break
                lit_len += 1
                i += 1
            out.append(lit_len - 1)
            out += data[lit_start : lit_start + lit_len]

    return bytes(out)


def rle_decode(data: bytes) -> bytes:
    """Decode an RLE-encoded byte sequence. See module docstring for format."""
    out = bytearray()
    i = 0

    while i < len(data):
        ctrl = data[i]
        i += 1

        if ctrl & 0x80:
            # Repeat run
            count = (ctrl & 0x7F) + 2
            out += bytes([data[i]]) * count
            i += 1
        else:
            # Literal run
            count = (ctrl & 0x7F) + 1
            out += data[i : i + count]
            i += count

    return bytes(out)


def generate_patch(file1: bytes, file2: bytes, patch_size: int) -> bytes:
    """
    Compare file1 and file2 in patch_size-byte blocks and return the binary
    patch stream.

    Layout: [patch_size: 1 byte] [record stream …]

    If file1's length is not a multiple of patch_size, full blocks are
    processed normally and the trailing partial block is emitted as a
    0x50 record.
    """
    lookup = _build_lookup(file2, patch_size)

    # 1-byte header: patch_size
    out = bytearray(patch_size.to_bytes(1, "big"))

    num_full = len(file1) // patch_size
    remainder = len(file1) % patch_size

    # Build a list of (record_type, data) tuples for full blocks
    records = []
    for idx in range(num_full):
        pos = idx * patch_size
        block = file1[pos : pos + patch_size]

        if pos + patch_size <= len(file2) and file2[pos : pos + patch_size] == block:
            records.append((0x43, b""))
        elif block in lookup:
            records.append((0x52, lookup[block].to_bytes(3, "big")))
        else:
            # Try XOR delta + RLE against reference at the same position
            ref_block = file2[pos : pos + patch_size] if pos + patch_size <= len(file2) else None
            if ref_block is not None:
                xor = bytes(a ^ b for a, b in zip(block, ref_block))
                rle = rle_encode(xor)
                if len(rle) < patch_size:
                    records.append((0x58, rle))
                else:
                    records.append((0x49, block))
            else:
                records.append((0x49, block))

    # Emit records, collapsing consecutive 0x43 runs into 0x44
    i = 0
    while i < len(records):
        rec_type, data = records[i]

        if rec_type == 0x43:
            # Count consecutive 0x43 records
            run = 1
            while i + run < len(records) and records[i + run][0] == 0x43:
                run += 1

            if run == 1:
                out.append(0x43)
            else:
                # Emit in chunks of up to 256 (stored as count-1)
                remaining = run
                while remaining > 1:
                    n = min(remaining, 256)
                    out.append(0x44)
                    out.append(n - 1)
                    remaining -= n
                if remaining == 1:
                    out.append(0x43)

            i += run
        elif rec_type == 0x58:
            out.append(0x58)
            out.append(len(data))
            out += data
            i += 1
        else:
            out.append(rec_type)
            out += data
            i += 1

    # Trailing partial block
    if remainder:
        tail = file1[num_full * patch_size:]
        out.append(0x50)
        out.append(remainder)
        out += tail

    return bytes(out)
