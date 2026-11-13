# -*- coding: utf-8 -*-
"""
ac6lib.py -- Ace Combat 6 (Xbox 360) DATA.TBL / DATA00.PAC / DATA01.PAC read-write library.

Layout recap
------------
DATA.TBL
    +0x00  u32 be  entry count
    +0x04  u32 be  unknown
    +0x08  N x 16 bytes, big endian: flags, offset, comp_size, uncomp_size

DATA00.PAC / DATA01.PAC
    Flat heaps of raw payloads. No index inside. Which heap a record lives in is
    encoded in flags bit 24 (0x01000000): clear -> DATA00, set -> DATA01.

Compression ("mode 1")
    raw deflate (wbits = -15), then XOR with a repeating 8-byte pad taken from the
    hexadecimal expansion of pi. For record index `i`, the pad is built from
    32-bit words W[2*(i % 256)+1] and W[2*(i % 256)+2]. Flag bit 16 (0x10000) marks
    a compressed record.

FHM container
    +0x00  "FHM "
    +0x10  u32 be  number of chunks
    +0x14  u32 be  chunk offset table (relative to container start)
    +..    u32 be  chunk size table, same order

Everything here is read-only safe: `store()` never overwrites existing bytes, it
appends the new payload at the end of the PAC and rewrites the TBL record only.
That keeps rollback to "restore DATA.TBL" trivial.
"""

import mmap
import os
import struct
import zlib

import numpy as np

FLAG_COMPRESSED = 0x00010000
FLAG_DATA01 = 0x01000000

ALIGN_TBL = 0x2000   # record offsets in the PAC are 0x2000 aligned in the original
ALIGN_FHM = 0x1000   # alignment used when appending a chunk inside an FHM blob


# --------------------------------------------------------------------------- pi pad

_PI_WORDS = None


def pi_words():
    """Return pi's fractional hex expansion as a list of 32-bit words.

    The game uses the *hexadecimal* digits of pi (3.243F6A88...), not decimal.
    Computed with high-precision arctan series; cached after the first call.
    """
    global _PI_WORDS
    if _PI_WORDS is not None:
        return _PI_WORDS

    from decimal import Decimal, getcontext

    getcontext().prec = 5400

    def atan_inv(x):
        x = Decimal(1) / Decimal(x)
        term = x
        total = x
        n = 1
        x2 = x * x
        while True:
            term = -term * x2
            n += 2
            d = term / n
            if abs(d) < Decimal(10) ** -5300:
                break
            total += d
        return total

    pi = 4 * (4 * atan_inv(5) - atan_inv(239))
    frac = pi - 3
    digits = []
    for _ in range(4200):
        frac *= 16
        k = int(frac)
        digits.append(format(k, "x"))
        frac -= k
    hx = "".join(digits)
    _PI_WORDS = [int(hx[i * 8:(i + 1) * 8], 16) for i in range(len(hx) // 8)]
    return _PI_WORDS


def pad_bytes(idx, n):
    """The repeating XOR pad for record `idx`, truncated/padded to n bytes."""
    w = pi_words()
    i = idx % 256
    pad = struct.pack(">II", w[2 * i + 1], w[2 * i + 2])
    return np.resize(np.frombuffer(pad, dtype=np.uint8), n).tobytes()


def decode_record(raw, idx):
    """XOR-off the pad and raw-inflate."""
    data = np.bitwise_xor(
        np.frombuffer(raw, dtype=np.uint8), np.resize(np.frombuffer(pad_bytes(idx, len(raw)), dtype=np.uint8), len(raw))
    ).tobytes()
    return zlib.decompress(data, -15)


def encode_record(data, idx, level=6):
    """raw-deflate then XOR with the pad. Output is what goes into the PAC."""
    co = zlib.compressobj(level, zlib.DEFLATED, -15)
    comp = co.compress(data) + co.flush()
    pad = np.frombuffer(pad_bytes(idx, len(comp)), dtype=np.uint8)
    return np.bitwise_xor(np.frombuffer(comp, dtype=np.uint8), pad).tobytes()


# --------------------------------------------------------------------------- FHM


def fhm_chunks(d):
    """Parse an FHM container. Returns (count, offsets, sizes) or None."""
    if d is None or len(d) < 20 or d[:4] != b"FHM ":
        return None
    nof = struct.unpack_from(">I", d, 16)[0]
    if nof <= 0 or nof > 200000:
        return None
    if 20 + nof * 8 > len(d):
        return None
    offs = [struct.unpack_from(">I", d, 20 + 4 * i)[0] for i in range(nof)]
    sizes = [struct.unpack_from(">I", d, 20 + 4 * nof + 4 * i)[0] for i in range(nof)]
    for i in range(nof):
        if offs[i] + sizes[i] > len(d):
            return None
    return nof, offs, sizes


def chunk(d, i):
    r = fhm_chunks(d)
    if not r:
        raise ValueError("not an FHM container")
    nof, offs, sizes = r
    return d[offs[i]:offs[i] + sizes[i]]


def set_chunk(d, i, payload, align=ALIGN_FHM):
    """Replace chunk `i` by appending `payload` and repointing the offset table.

    The old bytes stay in place, so the container only ever grows. Callers that
    need to stay small should decide what to append, not this function.
    """
    nof, offs, sizes = fhm_chunks(d)
    if i >= nof:
        raise ValueError("chunk %d >= count %d" % (i, nof))
    new_off = (len(d) + align - 1) // align * align
    out = bytearray(d)
    out.extend(b"\x00" * (new_off - len(d)))
    out.extend(payload)
    struct.pack_into(">I", out, 20 + i * 4, new_off)
    struct.pack_into(">I", out, 20 + nof * 4 + i * 4, len(payload))
    # keep the container size field consistent when the game relies on it
    return bytes(out)


def walk(d, path=(), depth=0, max_depth=8):
    """Yield (path, chunk_bytes) for every node in an FHM tree, depth first."""
    r = fhm_chunks(d)
    if not r or depth > max_depth:
        yield path, d
        return
    yield path, d
    nof, offs, sizes = r
    for i in range(nof):
        sub = d[offs[i]:offs[i] + sizes[i]]
        if fhm_chunks(sub):
            yield from walk(sub, path + (i,), depth + 1, max_depth)


# --------------------------------------------------------------------------- ISO

class IsoSlice:
    """Slicing facade over a file region, so ISO reads look like PAC reads."""

    def __init__(self, path, base, size):
        self.path = path
        self.base = base
        self.size = size
        self._f = open(path, "rb")

    def __getitem__(self, k):
        start = k.start or 0
        end = k.stop if k.stop is not None else self.size
        end = min(end, self.size)
        self._f.seek(self.base + start)
        return self._f.read(max(0, end - start))

    def close(self):
        self._f.close()


# --------------------------------------------------------------------------- payload


def extract_from(pacs, rows, idx):
    """Decompress record `idx` from an arbitrary list of (name, sliceable) heaps.

    Both heaps are probed on purpose: the flags bit is not always trustworthy on
    repacked builds, and validating against uncomp_size is what fixed 797 misread
    records during the original reverse engineering session.
    """
    fl, off, cs, us = rows[idx]
    if off == 0 or cs == 0:
        return None
    for _name, obj in pacs:
        try:
            raw = obj[off:off + cs]
        except Exception:
            continue
        if len(raw) != cs:
            continue
        try:
            data = decode_record(raw, idx) if (fl & FLAG_COMPRESSED) else raw
        except zlib.error:
            continue
        if len(data) == us:
            return data
    return None


# --------------------------------------------------------------------------- Game

class Game:
    def __init__(self, assets_dir, iso_path=None, iso_data00=None, iso_data01=None):
        self.dir = os.path.abspath(assets_dir)
        self.tbl_path = os.path.join(self.dir, "DATA.TBL")
        self.iso_path = iso_path
        self._handles = []
        self._pacs = []          # [(name, object_with_getitem)]
        self._open_local_pacs()
        if iso_path:
            self._open_iso(iso_data00, iso_data01)
        self.tbl_rows = self.read_rows(self.tbl_path)

    # -- pac handles -------------------------------------------------------
    def _open_local_pacs(self):
        for name in ("DATA00.PAC", "DATA01.PAC"):
            p = os.path.join(self.dir, name)
            if not os.path.exists(p):
                continue
            fh = open(p, "rb")
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            self._handles.append(fh)
            self._pacs.append((name, mm))

    def _open_iso(self, off0, off1):
        if off0 is None or off1 is None:
            raise ValueError("iso_data00 / iso_data01 offsets required with iso_path")
        self.iso_pacs = [
            ("ISO/DATA00", IsoSlice(self.iso_path, off0, os.path.getsize(self.iso_path) - off0)),
            ("ISO/DATA01", IsoSlice(self.iso_path, off1, os.path.getsize(self.iso_path) - off1)),
        ]

    def refresh(self):
        for fh in self._handles:
            try:
                fh.close()
            except Exception:
                pass
        for _, mm in self._pacs:
            try:
                mm.close()
            except Exception:
                pass
        self._handles = []
        self._pacs = []
        self._open_local_pacs()
        self.tbl_rows = self.read_rows(self.tbl_path)

    def close(self):
        self.refresh()
        for name, obj in self.iso_pacs if hasattr(self, "iso_pacs") else []:
            obj.close()

    # -- table -------------------------------------------------------------
    @staticmethod
    def read_rows(path):
        t = open(path, "rb").read()
        cnt = struct.unpack_from(">I", t, 0)[0]
        return [struct.unpack_from(">IIII", t, 8 + i * 16) for i in range(cnt)]

    @staticmethod
    def write_rows(path, rows):
        out = bytearray(struct.pack(">II", len(rows), 0))
        for r in rows:
            out.extend(struct.pack(">IIII", *r))
        with open(path, "wb") as fh:
            fh.write(out)

    # -- payload -----------------------------------------------------------
    def extract(self, idx, pacs=None, rows=None):
        """Decompress record `idx` from this install (or from `pacs`/`rows`)."""
        return extract_from(pacs if pacs is not None else self._pacs,
                            rows if rows is not None else self.tbl_rows, idx)

    def pac_for(self, idx, rows=None):
        rows = rows or self.tbl_rows
        fl = rows[idx][0]
        return "DATA01.PAC" if (fl & FLAG_DATA01) else "DATA00.PAC"

    def append_raw(self, idx, payload, rows):
        """Append already-encoded bytes to the owning PAC. Returns new offset."""
        fl = rows[idx][0]
        name = "DATA01.PAC" if (fl & FLAG_DATA01) else "DATA00.PAC"
        path = os.path.join(self.dir, name)
        size = os.path.getsize(path)
        pos = (size + ALIGN_TBL - 1) // ALIGN_TBL * ALIGN_TBL
        with open(path, "r+b") as fh:
            fh.seek(pos)
            fh.write(payload)
        return pos

    def store_blob(self, idx, blob, rows=None):
        """Encode `blob`, append it, and update the in-memory row. Does not save TBL."""
        rows = rows if rows is not None else self.tbl_rows
        fl = rows[idx][0]
        payload = encode_record(blob, idx)
        pos = self.append_raw(idx, payload, rows)
        rows[idx] = (fl, pos, len(payload), len(blob))
        return rows[idx]

    def save_tbl(self, rows=None):
        self.write_rows(self.tbl_path, rows if rows is not None else self.tbl_rows)
