"""Custom types and helpers for ORM models.

UUIDv7 (RFC 9562 §5.7) implemented inline using only stdlib — avoids pulling
in a third-party uuid7 package (which has brittle install on Windows and
limited maintenance). Format:

    field           bits
    -----           ----
    unix_ts_ms      48
    ver (=0b0111)    4
    rand_a          12
    var (=0b10)      2
    rand_b          62
                   ---
                   128

Monotonically sortable by time, dense in btree-index, suitable for PK.
"""

from __future__ import annotations

import os
import time
import uuid


def new_uuid() -> uuid.UUID:
    """Generate a UUIDv7.

    Centralised here so the implementation can be swapped (e.g. to ULID or
    CockroachDB-style timestamped UUIDs) without touching every model.
    """
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits

    buf = bytearray(os.urandom(16))
    # Bytes 0..5 — big-endian timestamp.
    buf[0] = (ts_ms >> 40) & 0xFF
    buf[1] = (ts_ms >> 32) & 0xFF
    buf[2] = (ts_ms >> 24) & 0xFF
    buf[3] = (ts_ms >> 16) & 0xFF
    buf[4] = (ts_ms >> 8) & 0xFF
    buf[5] = ts_ms & 0xFF
    # Byte 6 — высший nibble = version (7).
    buf[6] = (buf[6] & 0x0F) | 0x70
    # Byte 8 — высшие 2 бита = variant (10).
    buf[8] = (buf[8] & 0x3F) | 0x80

    return uuid.UUID(bytes=bytes(buf))
