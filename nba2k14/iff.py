"""Read, edit and rebuild NBA 2K14 .iff container files.

Layout (see docs/iff.md):

    header   magic 94 EF 3B FF, header size, end of chunk data (BE), chunk table,
             resource table (name hash, type hash, offset into each chunk)
    chunks   one per memory pool (DRAM = CPU data, VRAM = GPU data), each usually
             zlib-compressed behind a 16-byte "ZLIB" header
    trailer  tag 0x161517AA, length, name table (UTF-16) - copied unchanged

All names and types are stored as CRC32 of their ASCII text.
"""
import re
import struct
import zlib

MAGIC = 0xFF3BEF94
TRAILER_TAG = 0x161517AA


def name_hash(name):
    return zlib.crc32(name.encode("ascii"))


KNOWN_NAMES = ["DRAM", "VRAM", "SRAM", "TXTR", "SCNE", "RDAT", "AUDO", "MTRL", "TUNEDATA_RESOURCE",
               "FxTweakables", "ClothMesh", "UnAD", "NAME"]
POOL_NAMES = {name_hash(n): n for n in ("DRAM", "VRAM", "SRAM")}


class IffError(ValueError):
    pass


class Chunk:
    def __init__(self, index, desc_offset, words, packed):
        self.index = index
        self.desc_offset = desc_offset           # where its 8-word entry sits in the header
        self.pool_hash = words[0]
        self.align = words[2]
        self.alloc_size = words[3]
        self.level = words[4]
        self.offset = words[5]
        self.packed_size = words[6]
        self.flags = words[7]
        self.packed = packed                     # bytes as stored in the file
        self._data = None
        self.modified = False

    @property
    def pool(self):
        return POOL_NAMES.get(self.pool_hash, f"0x{self.pool_hash:08X}")

    @property
    def compressed(self):
        return self.packed[:4] == b"ZLIB"

    @property
    def data(self):
        if self._data is None:
            if self.compressed:
                raw_size = struct.unpack_from(">I", self.packed, 4)[0]
                self._data = bytearray(zlib.decompressobj().decompress(self.packed[16:]))
                if len(self._data) != raw_size:
                    raise IffError(f"chunk {self.index}: expected {raw_size} bytes, got {len(self._data)}")
            else:
                self._data = bytearray(self.packed)
        return self._data

    @data.setter
    def data(self, value):
        self._data = bytearray(value)
        self.modified = True

    def repack(self):
        if not self.modified:
            return self.packed
        if not self.compressed:
            return bytes(self._data)
        body = zlib.compress(bytes(self._data), min(9, max(1, self.level or 6)))
        header = b"ZLIB" + struct.pack(">III", len(self._data), 16 + len(body), self.level)
        return header + body


class Resource:
    def __init__(self, name_hash_, type_hash, offsets, entry_offset):
        self.name_hash = name_hash_
        self.type_hash = type_hash
        self.offsets = offsets                   # offset into chunk i (same order as chunks)
        self.entry_offset = entry_offset
        self.name = None
        self.type = None


class IffFile:
    def __init__(self, data, path=None):
        self.path = path
        self.raw = bytes(data)
        self._parse()

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(f.read(), path)

    # ----- parsing -----
    def _parse(self):
        d = self.raw
        if len(d) < 32 or struct.unpack_from("<I", d, 0)[0] != MAGIC:
            raise IffError("not an NBA 2K14 .iff file")
        self.header_size = struct.unpack_from("<I", d, 4)[0]
        self.data_end = struct.unpack_from(">I", d, 8)[0]
        self.chunk_count = struct.unpack_from("<I", d, 16)[0]
        self.version = struct.unpack_from("<I", d, 20)[0]
        self.resource_count = struct.unpack_from("<I", d, 24)[0]
        if not (32 <= self.header_size <= len(d)) or not (self.header_size <= self.data_end <= len(d)):
            raise IffError("bad header sizes")
        self.chunks = []
        for i in range(self.chunk_count):
            off = 32 + 32 * i
            words = struct.unpack_from("<8I", d, off)
            if words[0] != words[1]:
                raise IffError(f"chunk {i}: unexpected table entry")
            packed = d[words[5]:words[5] + words[6]]
            self.chunks.append(Chunk(i, off, words, packed))
        self.trailer = d[self.data_end:]
        self.names = self._read_names()
        self.resources = self._parse_resources()
        for r in self.resources:
            r.name = self.names.get(r.name_hash)
            r.type = self.names.get(r.type_hash)
        self.placement = self._parse_placement()
        self._virtual = None
        self.vram_dirty = False  # set when vram_view() was edited

    def _parse_placement(self):
        """VRAM placement table: count, then (code, final offset) pairs, right after the chunk table.

        code = (size << 10) | kind, kind 0x80+level for a mip level, 0x9F for a mip tail,
        0xBF for raw data (meshes). The VRAM chunk stores the pieces back to back in
        table order; the game moves each to its final offset. Returns [] when absent.
        """
        vram = self.chunk("VRAM")
        if vram is None:
            return []
        d = self.raw
        pos = 32 + 32 * self.chunk_count
        end = self.resources[0].entry_offset if self.resources else self.header_size
        if pos + 4 > end:
            return []
        n = struct.unpack_from("<I", d, pos)[0]
        if n == 0 or pos + 4 + 8 * n > end:
            return []
        pairs = [struct.unpack_from("<II", d, pos + 4 + 8 * i) for i in range(n)]
        if not all(code & 0x80 for code, _ in pairs):
            return []
        if sum(code >> 10 for code, _ in pairs) != len(vram.data):
            return []
        return pairs

    # ----- VRAM in final (as-loaded) layout -----
    def vram_view(self):
        """VRAM data with every piece at its final offset (what texture/mesh offsets refer to)."""
        if self._virtual is None:
            stored = self.chunk("VRAM").data
            if not self.placement:
                self._virtual = stored  # stored in final order already
            else:
                size = max(off + (code >> 10) for code, off in self.placement)
                view = bytearray(max(size, self.chunk("VRAM").alloc_size))
                p = 0
                for code, off in self.placement:
                    n = code >> 10
                    view[off:off + n] = stored[p:p + n]
                    p += n
                self._virtual = view
        return self._virtual

    def commit_vram_view(self):
        """Write edits made through vram_view() back into the stored (table-ordered) chunk."""
        if self._virtual is None:
            return
        vram = self.chunk("VRAM")
        if not self.placement:
            vram.modified = True
            return
        out = bytearray()
        for code, off in self.placement:
            out += self._virtual[off:off + (code >> 10)]
        vram.data = out

    def _read_names(self):
        names = {}
        for n in KNOWN_NAMES:
            names[name_hash(n)] = n
        for m in re.finditer(rb"(?:[\x20-\x7e]\x00){2,}", self.trailer):
            s = m.group().decode("utf-16le")
            names.setdefault(name_hash(s), s)
        return names

    def _parse_resources(self):
        """Resource entries are [name, type, count, offset * count] and fill the end of the header.

        The words between the chunk table and the entries vary by file type, so try each
        start position and keep the one that parses exactly resource_count entries.
        """
        d = self.raw
        words = struct.unpack_from(f"<{self.header_size // 4}I", d, 0)
        first = 8 + 8 * self.chunk_count
        for start in range(first, len(words)):
            pos, entries = start, []
            ok = True
            for _ in range(self.resource_count):
                if pos + 3 > len(words):
                    ok = False
                    break
                n = words[pos + 2]
                if not (1 <= n <= self.chunk_count) or pos + 3 + n > len(words):
                    ok = False
                    break
                offsets = list(words[pos + 3:pos + 3 + n])
                if any(o > max(c.alloc_size for c in self.chunks) for o in offsets):
                    ok = False
                    break
                entries.append(Resource(words[pos], words[pos + 1], offsets, pos * 4))
                pos += 3 + n
            if ok and all(w == 0 for w in words[pos:]):
                return entries
        return []

    # ----- access -----
    def chunk(self, pool):
        for c in self.chunks:
            if c.pool == pool:
                return c
        return None

    def find(self, name=None, type=None):
        return [r for r in self.resources
                if (name is None or r.name == name) and (type is None or r.type == type)]

    # ----- writing -----
    def build(self):
        """Return the file bytes. Unchanged chunks are copied byte-for-byte."""
        if self.vram_dirty:
            self.commit_vram_view()
            self.vram_dirty = False
        header = bytearray(self.raw[:self.header_size])
        body = bytearray()
        pos = self.header_size
        for c in self.chunks:
            packed = c.repack()
            struct.pack_into("<I", header, c.desc_offset + 20, pos)
            struct.pack_into("<I", header, c.desc_offset + 24, len(packed))
            if c.modified and c.compressed and len(c.data) > c.alloc_size:
                struct.pack_into("<I", header, c.desc_offset + 12, len(c.data))
            body += packed
            pos += len(packed)
        struct.pack_into(">I", header, 8, pos)
        return bytes(header) + bytes(body) + self.trailer

    def save(self, path):
        data = self.build()
        IffFile(data)  # re-parse as a sanity check before writing
        with open(path, "wb") as f:
            f.write(data)
        return len(data)

    def summary(self):
        lines = [f"{self.path or '<memory>'}: {len(self.raw)} bytes, version {self.version}, "
                 f"{self.chunk_count} chunks, {self.resource_count} resources"]
        for c in self.chunks:
            kind = f"zlib level {c.level}" if c.compressed else "stored"
            lines.append(f"  chunk {c.index} {c.pool:5} {c.alloc_size:>9} bytes ({kind}, {c.packed_size} packed, at 0x{c.offset:X})")
        for r in self.resources:
            name = r.name or f"0x{r.name_hash:08X}"
            typ = r.type or f"0x{r.type_hash:08X}"
            offs = ", ".join(f"{self.chunks[i].pool}+0x{o:X}" for i, o in enumerate(r.offsets))
            lines.append(f"  {typ:18} {name:32} {offs}")
        return "\n".join(lines)
