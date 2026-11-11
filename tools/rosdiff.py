"""Compare two NBA 2K14 roster files (.ROS) and show which player fields changed.

    python tools/rosdiff.py before.ROS after.ROS

Player records are bit-packed, 3644 bits each. Positions are reported relative to
each record's name field (the "anchor": last-name table id), which is how the
Ratings Tool addresses fields too (ratings start at anchor + 2812 bits).
For every changed bit range the old and new values are shown read as 8/16/32-bit
fields starting at that position, which usually makes the field obvious.
"""
import argparse
import os
import struct
import sys
import zlib

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

RECORD_BITS = 3644
NAME_ANCHOR0 = 7980 - 16  # Roster1.ROS: last-name table id of slot 0 (see docs)
SAVES = os.path.join(os.environ.get("APPDATA", ""), "2K Sports", "NBA 2K14", "Saves")


def bits_of(data):
    return np.unpackbits(np.frombuffer(data, np.uint8))


def get_bits(data, pos, n):
    v = 0
    for i in range(n):
        b = pos + i
        v = (v << 1) | ((data[b >> 3] >> (7 - (b & 7))) & 1)
    return v


def read_name(data, anchor):
    """Best effort: names are UTF-16 strings in bit-shifted string tables (see ratings_tool)."""
    try:
        import ratings_tool as R
        tables = R.find_name_tables(data)
        last = R.read_name(data, tables, get_bits(data, anchor, 16), get_bits(data, anchor + 16, 16))
        first = R.read_name(data, tables, get_bits(data, anchor + 32, 16), get_bits(data, anchor + 48, 16))
        name = f"{first or ''} {last or ''}".strip()
        return name
    except Exception:
        return ""


def runs(diff_bits):
    idx = np.flatnonzero(diff_bits)
    if not len(idx):
        return []
    out, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > 8:
            out.append((start, prev))
            start = i
        prev = i
    out.append((start, prev))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--anchor", type=int, default=NAME_ANCHOR0, help="bit position of slot 0's name anchor")
    args = ap.parse_args()
    paths = [p if os.path.isfile(p) else os.path.join(SAVES, p) for p in (args.before, args.after)]
    a, b = (open(p, "rb").read() for p in paths)
    if len(a) != len(b):
        raise SystemExit("Files differ in size - not comparable bit by bit.")
    for name, d in zip(paths, (a, b)):
        ok = zlib.crc32(d[4:]) == struct.unpack(">I", d[:4])[0]
        print(f"{os.path.basename(name)}: {len(d)} bytes, CRC {'ok' if ok else 'BAD'}")
    diff = bits_of(a[4:]) != bits_of(b[4:])
    found = runs(diff)
    print(f"{len(found)} changed bit ranges (CRC excluded)\n")
    for s, e in found:
        s += 32
        e += 32
        rel = s - args.anchor
        slot, off = divmod(rel, RECORD_BITS)
        anchor = args.anchor + slot * RECORD_BITS
        who = read_name(b, anchor) if 0 <= slot < 2000 else ""
        n = e - s + 1
        vals = ", ".join(f"{w}-bit {get_bits(a, s, w)} -> {get_bits(b, s, w)}" for w in (8, 16, 32))
        print(f"bits {s}-{e} ({n} bits)  slot {slot} {who!r}  record offset {off:+d}  | {vals}")


if __name__ == "__main__":
    main()
