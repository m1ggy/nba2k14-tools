"""Player records in NBA 2K14 saves (.CMG / .FXG) and roster files (.ROS).

See docs/roster.md for the field map. Records are 3644 bits; positions are bit
offsets from each record's name anchor (last-name string table id).
"""
import os
import shutil
import struct
import time
import zlib
from collections import Counter

import numpy as np

RECORD_BITS = 3644

FIELDS = {
    # name: (offset, kind, width)
    "height_cm": (96, "float", 32),
    "weight_lb": (128, "float", 32),
    "face_id": (224, "uint", 16),
    "jersey": (268, "uint", 8),
    "position": (424, "uint", 3),
}
RATINGS_OFFSET = 2812
RATING_COUNT = 42
POSITIONS = ["PG", "SG", "SF", "PF", "C"]


# ---------------------------------------------------------------- bits
def get_bits(data, pos, n):
    v = 0
    for i in range(n):
        b = pos + i
        v = (v << 1) | ((data[b >> 3] >> (7 - (b & 7))) & 1)
    return v


def put_bits(data, pos, n, value):
    for i in range(n):
        b = pos + i
        bit = (value >> (n - 1 - i)) & 1
        if bit:
            data[b >> 3] |= 1 << (7 - (b & 7))
        else:
            data[b >> 3] &= ~(1 << (7 - (b & 7))) & 0xFF


def _shift_left(data, s):
    if s == 0:
        return bytes(data)
    n = len(data) * 8
    return ((int.from_bytes(data, "big") << s) & ((1 << n) - 1)).to_bytes(len(data), "big")


# ---------------------------------------------------------------- strings
def _find_name_tables(data):
    """{table id: byte offset} of UTF-16 name tables (same rules as the Ratings Tool)."""
    import re
    groups = []
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){2,40}\x00\x00", data):
        if groups and m.start() - groups[-1][1] <= 64:
            groups[-1][1] = m.end()
            groups[-1][2].append(m.group())
        else:
            groups.append([m.start(), m.end(), [m.group()]])
    tables = {}
    for start, end, strings in groups:
        uniq = set(strings)
        if len(uniq) < 50:
            continue
        good = sum(sum(c.isalpha() for c in s.decode("utf-16le", "ignore")) >= 2 for s in uniq)
        if good / len(uniq) < 0.5:
            continue
        tid = int.from_bytes(data[start - 5:start - 3], "big")
        tables[tid] = start
    return tables


def _read_string(data, tables, tid, off):
    base = tables.get(tid)
    if base is None:
        return None
    p = base + 2 * off
    end = p
    while end + 1 < len(data) and data[end:end + 2] != b"\0\0":
        end += 2
        if end - p > 120:
            return None
    try:
        return bytes(data[p:end]).decode("utf-16le")
    except UnicodeDecodeError:
        return None


# ---------------------------------------------------------------- file
class Player:
    def __init__(self, roster, index, anchor, first, last):
        self.roster = roster
        self.index = index
        self.anchor = anchor
        self.first = first
        self.last = last

    @property
    def name(self):
        return f"{self.first} {self.last}".strip()

    def get(self, field):
        off, kind, width = FIELDS[field]
        v = get_bits(self.roster.data, self.anchor + off, width)
        return struct.unpack(">f", struct.pack(">I", v))[0] if kind == "float" else v

    def set(self, field, value):
        off, kind, width = FIELDS[field]
        if kind == "float":
            v = struct.unpack(">I", struct.pack(">f", float(value)))[0]
        else:
            v = int(value)
            if not 0 <= v < (1 << width):
                raise ValueError(f"{field} must be 0..{(1 << width) - 1}")
        put_bits(self.roster.data, self.anchor + off, width, v)

    def ratings(self):
        return [get_bits(self.roster.data, self.anchor + RATINGS_OFFSET + 8 * i, 8) for i in range(RATING_COUNT)]

    def set_ratings(self, raws):
        for i, v in enumerate(raws):
            put_bits(self.roster.data, self.anchor + RATINGS_OFFSET + 8 * i, 8, max(0, min(255, int(v))))


STRUCT_RECORD = 484          # bytes per player in the game's built-in rosters (roster.iff etc.)
STRUCT_FIELDS = {            # byte offset, struct format (little-endian)
    "height_cm": (12, "<f"),
    "weight_lb": (16, "<f"),
    "face_id": (28, "<H"),
}
STRUCT_RATINGS = 384


def _rel_ptr(data, pos):
    """Self-relative 32-bit pointer stored as (distance + 1); returns the target or None."""
    v = struct.unpack_from("<I", data, pos)[0]
    if not v & 1:
        return None
    t = pos + v - 1
    return t if 0 <= t < len(data) else None


def _utf16_at(data, pos, limit=120):
    end = pos
    while end + 1 < len(data) and data[end:end + 2] != b"\0\0":
        end += 2
        if end - pos > limit:
            return None
    try:
        s = bytes(data[pos:end]).decode("utf-16le")
    except UnicodeDecodeError:
        return None
    return s if all(32 <= ord(c) < 0x2500 for c in s) else None


class StructPlayer(Player):
    """A player in the built-in roster layout: byte-aligned 484-byte records."""

    def get(self, field):
        if field not in STRUCT_FIELDS:
            raise KeyError(f"{field} isn't mapped for built-in rosters yet")
        off, fmt = STRUCT_FIELDS[field]
        return struct.unpack_from(fmt, self.roster.data, self.anchor + off)[0]

    def set(self, field, value):
        if field not in STRUCT_FIELDS:
            raise KeyError(f"{field} isn't mapped for built-in rosters yet")
        off, fmt = STRUCT_FIELDS[field]
        struct.pack_into(fmt, self.roster.data, self.anchor + off, float(value) if fmt == "<f" else int(value))

    def ratings(self):
        o = self.anchor + STRUCT_RATINGS
        return list(self.roster.data[o:o + RATING_COUNT])

    def set_ratings(self, raws):
        o = self.anchor + STRUCT_RATINGS
        self.roster.data[o:o + RATING_COUNT] = bytes(max(0, min(255, int(v))) for v in raws)


def _scan_struct(roster):
    """Find the 484-byte records: consecutive records whose first two words point to names."""
    d = roster.data

    def names_at(pos):
        lt, ft = _rel_ptr(d, pos), _rel_ptr(d, pos + 4)
        if lt is None or ft is None:
            return None
        last, first = _utf16_at(d, lt), _utf16_at(d, ft)
        return (first, last) if last is not None and first is not None and (first or last) else None

    start = None
    for pos in range(0, min(len(d) - 3 * STRUCT_RECORD, 1 << 20), 4):
        if names_at(pos) and names_at(pos + STRUCT_RECORD) and names_at(pos + 2 * STRUCT_RECORD):
            start = pos
            break
    if start is None:
        raise ValueError("No player records found in this built-in roster.")
    players, i = [], 0
    while start + (i + 1) * STRUCT_RECORD <= len(d):
        pos = start + i * STRUCT_RECORD
        n = names_at(pos)
        if n is None:
            break
        players.append(StructPlayer(roster, i, pos, n[0], n[1]))
        i += 1
    return players


class Roster:
    """A save or roster file with its player records."""

    def __init__(self, path):
        self.path = path
        self.iff = None
        if path.lower().endswith(".iff"):
            # the game's built-in rosters (roster.iff etc.): one ROST resource in the DRAM chunk
            from .iff import IffFile
            self.iff = IffFile.load(path)
            self.data = self.iff.chunk("DRAM").data
            self.crc_ok = True
            self.players = _scan_struct(self)
            return
        with open(path, "rb") as f:
            self.data = bytearray(f.read())
        self.crc_ok = zlib.crc32(self.data[4:]) == struct.unpack(">I", self.data[:4])[0]
        self.players = self._scan()

    @property
    def built_in(self):
        return self.iff is not None

    def _scan(self):
        """Try every bit alignment of the name tables (roster files use 5) and keep the best."""
        best = []
        for shift in range(8):
            sd = _shift_left(self.data, shift)
            tables = _find_name_tables(sd)
            if not tables:
                continue
            try:
                found = self._scan_with(sd, tables, shift)
            except ValueError:
                continue
            if len(found) > len(best):
                best = found
        if not best:
            raise ValueError("No player records found in this file.")
        return best

    def _scan_with(self, sd, tables, shift):
        tids = list(tables)
        tid_index = np.full(65536, -1, np.int16)
        valid = np.zeros((len(tids), 65536), bool)
        for i, tid in enumerate(tids):
            tid_index[tid] = i
            seg = np.frombuffer(sd[tables[tid]:tables[tid] + 2 * 65536], dtype="<u2")
            valid[i, 0] = True
            z = np.flatnonzero(seg == 0)
            valid[i, (z + 1)[(z + 1) < 65536]] = True
        big = int.from_bytes(sd, "big")
        nbits = len(sd) * 8
        hits = []
        for s in range(8):
            b = sd if s == 0 else ((big << s) & ((1 << nbits) - 1)).to_bytes(len(sd), "big")
            a = np.frombuffer(b, np.uint8).astype(np.int32)
            w = (a[:-1] << 8) | a[1:]
            n = len(w) - 6
            t1, t2 = tid_index[w[:n]], tid_index[w[4:n + 4]]
            k = np.flatnonzero((t1 >= 0) & (t2 >= 0))
            k = k[valid[t1[k], w[k + 2]] & valid[t2[k], w[k + 6]] & ((w[k + 2] != 0) | (w[k + 6] != 0))]
            hits.extend((8 * k + s).tolist())
        if not hits:
            raise ValueError("No player records found.")
        residue = Counter(h % RECORD_BITS for h in hits).most_common(1)[0][0]
        anchors = sorted(h for h in hits if h % RECORD_BITS == residue)
        players = []
        first_anchor = anchors[0]
        for p in anchors:
            if p + shift + RATINGS_OFFSET + 8 * RATING_COUNT > nbits:
                continue
            last = _read_string(sd, tables, get_bits(sd, p, 16), get_bits(sd, p + 16, 16))
            first = _read_string(sd, tables, get_bits(sd, p + 32, 16), get_bits(sd, p + 48, 16))
            if first is None or last is None or not (first or last):
                continue
            # positions in the original file are `shift` bits later
            players.append(Player(self, (p - first_anchor) // RECORD_BITS, p + shift, first, last))
        return players

    def find(self, text):
        t = text.lower().strip()
        return [p for p in self.players if t in p.name.lower()]

    def save(self, backup=True, path=None):
        """Write back (save files: checksum fixed). For .iff rosters pass `path` to write a copy
        (e.g. into a mod) instead of the game file."""
        if self.iff is not None:
            self.iff.chunk("DRAM").modified = True
            out = path or self.path
            self.iff.save(out)
            return None
        struct.pack_into(">I", self.data, 0, zlib.crc32(self.data[4:]))
        bak = None
        if backup:
            bak = self.path + time.strftime(".%Y%m%d-%H%M%S.bak")
            shutil.copy2(self.path, bak)
        with open(self.path, "wb") as f:
            f.write(self.data)
        return bak


def height_text(cm):
    inches = round(cm / 2.54)
    return f"{inches // 12}'{inches % 12}\""


def rating_from_raw(raw):
    return round(raw / 3 + 25)


def raw_from_rating(r):
    return 255 if r >= 110 else max(0, min(255, round((r - 25) * 3)))
