# ZiPPer - what it says: Zissis Papadopoulos PatchER

A compact binary file patch generator and rebuilder. Given two versions of a binary file, **zipper** produces a minimal patch using block comparison, offset relocation, XOR-delta, and RLE compression. The patch can later be applied against the original file to exactly reconstruct the newer version.

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PyQt5](https://img.shields.io/badge/PyQt5-5.15%2B-41CD52?logo=qt&logoColor=white)](https://pypi.org/project/PyQt5/)
[![pyserial](https://img.shields.io/badge/pyserial-3.5%2B-lightgrey?logo=pypi&logoColor=white)](https://pypi.org/project/pyserial/)
[![zlib](https://img.shields.io/badge/zlib-stdlib-informational)](https://docs.python.org/3/library/zlib.html)

---

## How It Works

### Patch Generation (`main.py` + `zipper.py`)

The generator splits **file2** (the new version) into fixed-size blocks (`patch_size`, default 64 bytes, must be a positive multiple of 8). For each block it tries—in order of preference—the most compact encoding:

| Record | Byte | Description |
|--------|------|-------------|
| `COPY_SAME`   | `0x43` | Block matches file1 at the same byte offset — 1 byte overhead. |
| `COPY_RUN`    | `0x44 count` | Run of 2–256 consecutive same-position matches — 2 bytes overhead. |
| `COPY_OFFSET` | `0x52 offset[3]` | Block found in file1 at a *different* offset (3-byte BE offset) — 4 bytes overhead. |
| `XOR_RLE`     | `0x58 len rle…` | XOR delta against the reference block, RLE-compressed — wins over `RAW` when smaller. |
| `RAW`         | `0x49 data…` | Block not found anywhere in file1; raw data embedded verbatim — `1 + patch_size` bytes. |
| `PARTIAL`     | `0x50 len data…` | Trailing partial block shorter than `patch_size`. |

The patch is prefixed with a 1-byte header holding `patch_size`, making each patch self-describing. After the record stream, a 4-byte big-endian CRC-32 of the *target* file is appended for integrity verification.

#### RLE Format (inside `0x58` records)

| Control byte | Meaning |
|---|---|
| `0x00`–`0x7F` | **Literal run**: `(byte & 0x7F) + 1` bytes follow verbatim (1–128 bytes). |
| `0x80`–`0xFF` | **Repeat run**: repeat the next byte `(byte & 0x7F) + 2` times (2–129 repetitions). |

#### Block Lookup Strategy

The generator builds a hash map from every `patch_size`-byte slice of file1 to its earliest offset. **Aligned** positions (multiples of `patch_size`) are indexed first so they win ties, minimising seeks when the patch is applied.

---

### Patch Application (`builder.py`)

The builder reads the patch (`.bin`), validates CRC-32, and replays each record against the reference file to reconstruct the target:

- **`.bin`** — raw patch bytes + 4-byte trailing CRC-32.

---

## Project Structure

```
zipper/
├── zipper.py      # Patch generation: block lookup, RLE encoder/decoder, record emitter + CLI
├── builder.py     # Patch application: .bin decoding, CRC validation, file reconstruction + CLI
├── encoder.py     # Serialises the patch stream to .bin with a CRC-32 trailer
├── main.py        # Alternative CLI entry point (same as zipper.py)
├── __init__.py
└── requirements.txt
```

---

## Installation

```bash
pip install -r requirements.txt
```

> **Python 3.10 or later** is required (uses built-in generic types such as `tuple[bytes, int]`).

---

## Usage

### Generate a patch

```bash
python zipper.py <old_file.bin> <new_file.bin>
```

You will be prompted for the block size (default: 64 bytes). The tool produces:

| Output file | Description |
|---|---|
| `<old_file>_patch.bin` | Compact binary patch with CRC-32 trailer |

**Example:**

```
$ python zipper.py firmware_v1.bin firmware_v2.bin
File 1 : firmware_v1.bin  (131,072 bytes)
File 2 : firmware_v2.bin  (131,072 bytes)

Patch size in bytes (multiple of 8) [64]:

Building patch  (patch_size=64 B) …

Done.
  Patch binary : firmware_v1_patch.bin  (4,218 bytes)
  Compression  : 4,218 B patch vs 131,072 B firmware_v2.bin  →  96.8%
```

### Apply a patch

```bash
python builder.py <reference.bin> <patch.bin> [output.bin]
```

| Argument | Required | Description |
|---|---|---|
| `reference.bin` | Yes | The *old* file (same as `<file1>` passed to `zipper.py`) |
| `patch.bin` | Yes | The patch produced by `zipper.py` |
| `output.bin` | No | Output path — defaults to `rebuilt.bin` |

**Example:**

```
$ python builder.py firmware_v1.bin firmware_v1_patch.bin firmware_v2_rebuilt.bin
Reference : firmware_v1.bin  (131,072 bytes)
Patch     : firmware_v1_patch.bin
Output    : firmware_v2_rebuilt.bin

Decoding patch (binary format) …
  Raw patch : 4,214 bytes  |  patch_size = 64
Applying patch …
  CRC-32 OK (0x9a3f21bc)

Done.  Reconstructed : firmware_v2_rebuilt.bin  (131,072 bytes)
```

---

## Patch Size Trade-offs

| Smaller `patch_size` | Larger `patch_size` |
|---|---|
| More granular block matching | Fewer records, smaller overhead |
| Better for files with many small scattered changes | Better for files with large identical regions |
| Slower (more iterations) | Faster |

The default of **64 bytes** works well for most firmware or binary assets. Must be a positive multiple of 8.

---

## Limitations

- Maximum addressable offset for `COPY_OFFSET` records: **16 MB** (`0xFFFFFF`). Files larger than ~16 MB may fall back to `RAW` or `XOR_RLE` records for blocks beyond that range.
- The patch file is loaded entirely into memory for CRC validation before reconstruction begins.
