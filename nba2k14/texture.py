"""Textures inside .iff files (TXTR resources): decode to RGBA and encode back.

TXTR record in the DRAM chunk (see docs/iff.md):
    +0x00  name hash (CRC32)
    +0x08  format: FourCC b"DXT1" / b"DXT5", or a D3D format number (21 = A8R8G8B8)
    +0x68  width  (u16)
    +0x6A  height (u16)
Pixel data sits in the VRAM chunk at the resource's VRAM offset: the full
mipmap chain, largest first, linear (not swizzled).
"""
import struct

import numpy as np

DXT1 = b"DXT1"
DXT5 = b"DXT5"
A8R8G8B8 = struct.pack("<I", 21)
X8R8G8B8 = struct.pack("<I", 22)
SUPPORTED = (DXT1, DXT5, A8R8G8B8, X8R8G8B8)


def format_name(fmt):
    if fmt in (DXT1, DXT5):
        return fmt.decode()
    return {21: "A8R8G8B8", 22: "X8R8G8B8", 28: "A8", 41: "P8", 50: "L8"}.get(
        struct.unpack("<I", fmt)[0], f"format {struct.unpack('<I', fmt)[0]}")


def level_size(fmt, w, h):
    if fmt in (DXT1, DXT5):
        blocks = max(1, (w + 3) // 4) * max(1, (h + 3) // 4)
        return blocks * (8 if fmt == DXT1 else 16)
    return w * h * 4


def mip_chain(fmt, w, h):
    """[(width, height, bytes)] down to 1x1."""
    levels = []
    while True:
        levels.append((w, h, level_size(fmt, w, h)))
        if w == 1 and h == 1:
            return levels
        w, h = max(1, w // 2), max(1, h // 2)


# ---------------------------------------------------------------- decoding
def _rgb565(c):
    r = ((c >> 11) & 31).astype(np.uint16)
    g = ((c >> 5) & 63).astype(np.uint16)
    b = (c & 31).astype(np.uint16)
    return np.stack([(r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31], -1).astype(np.uint8)


def _decode_color_blocks(blk, four_color_only):
    """blk: (n, 8) uint8 BC1 colour blocks -> (n, 16, 4) RGBA."""
    c0 = blk[:, 0].astype(np.uint16) | (blk[:, 1].astype(np.uint16) << 8)
    c1 = blk[:, 2].astype(np.uint16) | (blk[:, 3].astype(np.uint16) << 8)
    p0, p1 = _rgb565(c0).astype(np.int32), _rgb565(c1).astype(np.int32)
    four = (c0 > c1) | four_color_only
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1).astype(np.uint8)              # (n, 4, 3)
    alpha = np.full((len(blk), 4), 255, np.uint8)
    alpha[:, 3] = np.where(four, 255, 0)
    bits = blk[:, 4:8].astype(np.uint32)
    idx_word = bits[:, 0] | (bits[:, 1] << 8) | (bits[:, 2] << 16) | (bits[:, 3] << 24)
    idx = ((idx_word[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3).astype(np.intp)
    rows = np.arange(len(blk))[:, None]
    rgb = pal[rows, idx]
    a = alpha[rows, idx]
    return np.concatenate([rgb, a[..., None]], -1)


def _decode_alpha_blocks(blk):
    """blk: (n, 8) uint8 BC3 alpha blocks -> (n, 16) alpha."""
    a0 = blk[:, 0].astype(np.int32)
    a1 = blk[:, 1].astype(np.int32)
    pal = np.zeros((len(blk), 8), np.int32)
    pal[:, 0], pal[:, 1] = a0, a1
    eight = a0 > a1
    for i in range(1, 7):
        pal[:, i + 1] = np.where(eight, ((7 - i) * a0 + i * a1) // 7, 0)
    for i in range(1, 5):
        pal[:, i + 1] = np.where(eight, pal[:, i + 1], ((5 - i) * a0 + i * a1) // 5)
    pal[:, 6] = np.where(eight, pal[:, 6], 0)
    pal[:, 7] = np.where(eight, pal[:, 7], 255)
    bits = np.zeros(len(blk), np.uint64)
    for i in range(6):
        bits |= blk[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    idx = ((bits[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.intp)
    return pal[np.arange(len(blk))[:, None], idx].astype(np.uint8)


def _blocks_to_image(px, w, h):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w]


def decode(fmt, w, h, data):
    """Top mip level -> (h, w, 4) uint8 RGBA."""
    size = level_size(fmt, w, h)
    buf = np.frombuffer(bytes(data[:size]), np.uint8)
    if fmt == DXT1:
        return _blocks_to_image(_decode_color_blocks(buf.reshape(-1, 8), False), w, h)
    if fmt == DXT5:
        blk = buf.reshape(-1, 16)
        px = _decode_color_blocks(blk[:, 8:], True)
        px[..., 3] = _decode_alpha_blocks(blk[:, :8])
        return _blocks_to_image(px, w, h)
    if fmt in (A8R8G8B8, X8R8G8B8):
        bgra = buf.reshape(h, w, 4)
        rgba = bgra[..., [2, 1, 0, 3]].copy()
        if fmt == X8R8G8B8:
            rgba[..., 3] = 255
        return rgba
    raise ValueError(f"can't decode {format_name(fmt)} yet")


# ---------------------------------------------------------------- encoding
def _image_to_blocks(img):
    h, w = img.shape[:2]
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    pad = np.zeros((bh * 4, bw * 4, 4), np.uint8)
    pad[:h, :w] = img
    if h < 4 or w < 4:  # repeat edge pixels into tiny levels
        pad[:, w:] = pad[:, w - 1:w]
        pad[h:, :] = pad[h - 1:h, :]
    return pad.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)


def _to565(rgb):
    r = (rgb[..., 0].astype(np.uint32) * 31 + 127) // 255
    g = (rgb[..., 1].astype(np.uint32) * 63 + 127) // 255
    b = (rgb[..., 2].astype(np.uint32) * 31 + 127) // 255
    return ((r << 11) | (g << 5) | b).astype(np.uint16)


def _encode_color_blocks(px):
    """px: (n, 16, 4) -> (n, 8) BC1 blocks, always 4-colour mode."""
    rgb = px[..., :3].astype(np.float32)
    mean = rgb.mean(1, keepdims=True)
    centered = rgb - mean
    cov = np.einsum("nki,nkj->nij", centered, centered)
    axis = np.ones((len(px), 3), np.float32)
    for _ in range(4):  # power iteration for the main colour direction
        axis = np.einsum("nij,nj->ni", cov, axis)
        axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-6)
    proj = np.einsum("nki,ni->nk", centered, axis)
    lo = mean[:, 0] + axis * proj.min(1, keepdims=True)
    hi = mean[:, 0] + axis * proj.max(1, keepdims=True)
    c0 = _to565(np.clip(hi + 0.5, 0, 255).astype(np.uint8))
    c1 = _to565(np.clip(lo + 0.5, 0, 255).astype(np.uint8))
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    equal = c0 == c1
    c1 = np.where(equal & (c0 > 0), c0 - 1, c1)
    c0 = np.where(equal & (c0 == 0), 1, c0)
    p0, p1 = _rgb565(c0).astype(np.float32), _rgb565(c1).astype(np.float32)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)          # (n, 4, 3)
    dist = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)             # (n, 16, 4)
    idx = dist.argmin(-1).astype(np.uint32)
    word = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    out = np.zeros((len(px), 8), np.uint8)
    out[:, 0], out[:, 1] = c0 & 255, c0 >> 8
    out[:, 2], out[:, 3] = c1 & 255, c1 >> 8
    for i in range(4):
        out[:, 4 + i] = (word >> (8 * i)) & 255
    return out


def _encode_alpha_blocks(alpha):
    """alpha: (n, 16) -> (n, 8) BC3 alpha blocks (8-value mode)."""
    a = alpha.astype(np.int32)
    a0, a1 = a.max(1), a.min(1)
    same = a0 == a1
    a0 = np.where(same & (a0 < 255), a0 + 1, a0)
    a1 = np.where(same & (a0 == a1), a1 - 1, a1)
    pal = np.stack([a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)], 1)
    idx = np.abs(a[:, :, None] - pal[:, None, :]).argmin(-1).astype(np.uint64)
    bits = (idx << (np.uint64(3) * np.arange(16, dtype=np.uint64))).sum(1, dtype=np.uint64)
    out = np.zeros((len(alpha), 8), np.uint8)
    out[:, 0], out[:, 1] = a0, a1
    for i in range(6):
        out[:, 2 + i] = ((bits >> np.uint64(8 * i)) & np.uint64(255)).astype(np.uint8)
    return out


def _encode_level(fmt, img):
    if fmt == DXT1:
        return _encode_color_blocks(_image_to_blocks(img)).tobytes()
    if fmt == DXT5:
        px = _image_to_blocks(img)
        return np.concatenate([_encode_alpha_blocks(px[..., 3]), _encode_color_blocks(px)], 1).tobytes()
    if fmt in (A8R8G8B8, X8R8G8B8):
        return img[..., [2, 1, 0, 3]].astype(np.uint8).tobytes()
    raise ValueError(f"can't encode {format_name(fmt)} yet")


def _downsample(img):
    h, w = img.shape[:2]
    nh, nw = max(1, h // 2), max(1, w // 2)
    f = img.astype(np.float32)
    if h > 1:
        f = (f[0:nh * 2:2] + f[1:nh * 2:2]) / 2
    if w > 1:
        f = (f[:, 0:nw * 2:2] + f[:, 1:nw * 2:2]) / 2
    return np.clip(f + 0.5, 0, 255).astype(np.uint8)


def encode(fmt, img, levels=None):
    """(h, w, 4) RGBA -> bytes of the full mipmap chain (or the first `levels` levels)."""
    img = np.ascontiguousarray(img, np.uint8)
    h, w = img.shape[:2]
    chain = mip_chain(fmt, w, h)[:levels]
    out = bytearray()
    for i, (lw, lh, size) in enumerate(chain):
        level = _encode_level(fmt, img)
        assert len(level) == size, (lw, lh, len(level), size)
        out += level
        img = _downsample(img)
    return bytes(out)


# ---------------------------------------------------------------- iff glue
class Texture:
    """A TXTR resource inside an IffFile, or a texture embedded in a SCNE (scene).

    Texture records hold their VRAM location at +0x74 as (offset + 1), relative to
    the owning resource's VRAM offset.
    """

    def __init__(self, iff, resource, record=None, label=None):
        if record is None and resource.type != "TXTR":
            raise ValueError(f"{resource.name} is not a texture")
        self.iff = iff
        self.resource = resource
        self.record = resource.offsets[0] if record is None else record
        self.label = label
        dram = iff.chunk("DRAM").data
        o = self.record
        self.format = bytes(dram[o + 8:o + 12])
        self.width, self.height = struct.unpack_from("<HH", dram, o + 0x68)
        ptr = struct.unpack_from("<I", dram, o + 0x74)[0]
        base = resource.offsets[1] if len(resource.offsets) > 1 else None
        self.vram_offset = base + ptr - 1 if (base is not None and ptr & 1) else None

    @property
    def name(self):
        return self.label or self.resource.name

    @property
    def size(self):
        return sum(s for _, _, s in mip_chain(self.format, self.width, self.height))

    @property
    def editable(self):
        if self.vram_offset is None or self.format not in SUPPORTED:
            return False
        return self.vram_offset + self.size <= len(self.iff.vram_view())

    def image(self):
        vram = self.iff.vram_view()
        return decode(self.format, self.width, self.height, vram[self.vram_offset:self.vram_offset + self.size])

    def set_image(self, img):
        img = np.asarray(img)
        if img.shape[:2] != (self.height, self.width):
            raise ValueError(f"{self.name} must be {self.width}x{self.height}, got {img.shape[1]}x{img.shape[0]}")
        if img.shape[2] == 3:
            img = np.concatenate([img, np.full(img.shape[:2] + (1,), 255, np.uint8)], -1)
        data = encode(self.format, img)
        vram = self.iff.vram_view()
        end = self.vram_offset + len(data)
        if end > len(vram):
            raise ValueError("texture data runs past the end of VRAM")
        vram[self.vram_offset:end] = data
        if self.iff.placement:
            self.iff.vram_dirty = True
        else:
            self.iff.chunk("VRAM").modified = True


def _scene_records(iff, scene):
    """DRAM offsets of texture records inside a scene's DRAM range."""
    dram = bytes(iff.chunk("DRAM").data)
    start = scene.offsets[0]
    later = [r.offsets[0] for r in iff.resources if r.offsets and r.offsets[0] > start]
    end = min(later) if later else len(dram)
    found = []
    pos = start
    while True:
        i = min((p for p in (dram.find(DXT1, pos, end), dram.find(DXT5, pos, end)) if p >= 0), default=-1)
        if i < 0:
            return found
        rec = i - 8
        if rec >= start and dram[i + 20:i + 24] == dram[i:i + 4] and struct.unpack_from("<I", dram, rec + 0x74)[0] & 1:
            found.append(rec)
        pos = i + 4


def textures(iff):
    """All textures: standalone TXTR resources, then textures embedded in scenes
    (named '<scene>#<n>' plus the record's name when the file lists it)."""
    out = [Texture(iff, r) for r in iff.resources if r.type == "TXTR"]
    if iff.chunk("VRAM") is None:
        return out
    dram = iff.chunk("DRAM").data
    for scene in iff.resources:
        if scene.type != "SCNE" or len(scene.offsets) < 2:
            continue
        for n, rec in enumerate(_scene_records(iff, scene)):
            h = struct.unpack_from("<I", dram, rec)[0]
            extra = iff.names.get(h)
            if extra:
                extra = extra.replace("\\", "/").split("/")[-1]
            label = f"{scene.name}#{n}" + (f" {extra}" if extra else "")
            out.append(Texture(iff, scene, record=rec, label=label))
    return out
