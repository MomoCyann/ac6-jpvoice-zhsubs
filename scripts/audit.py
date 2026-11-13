# -*- coding: utf-8 -*-
"""
audit.py -- verify that no subtitle slot is still the stock Japanese one.

Two independent checks:

1. Internal consistency: every language container must have slot0 == slot1.
2. Baseline comparison (needs the unlocalized ISO): any language slot that is
   byte-identical to a stock Japanese slot means the localization never touched
   that copy. This is how the duplicated mission packages (#103/#109/#111/#115/
   #117, byte-identical to stock #9/#15/#17/#21/#23) were found.

Usage
-----
    python audit.py --dir ".../assets"
    python audit.py --dir ".../assets" \
                    --iso "X:/Ace Combat 6 ....iso" \
                    --iso-tbl "DATA.TBL.orig"

`--iso-tbl` must be a DATA.TBL dumped from the *unlocalized* disc. Without it the
baseline check is skipped and only the internal consistency check runs.

The ISO byte offsets below belong to "Ace Combat 6 - Fires of Liberation (USA,
Japan) (En,Ja)". Other dumps need them re-derived (see docs/FORMAT.md).
"""

import argparse
import hashlib
import os
import sys

from ac6lib import Game, IsoSlice, chunk, extract_from, fhm_chunks

ISO_DATA00 = 0x06197F800
ISO_DATA01 = 0x154E3A000

CJK_NTXR_MIN = 100_000   # NTXR payload above this is the 2048x256 CJK atlas
MAX_DEPTH = 4


def slot_info(block):
    """(has CAPT as chunk 0, largest NTXR payload inside) for one language block."""
    r = fhm_chunks(block)
    if not r:
        return False, 0
    nof, offs, sizes = r
    has_capt = block[offs[0]:offs[0] + 4] == b"CAPT"
    ntx = 0
    for i in range(nof):
        sub = block[offs[i]:offs[i] + sizes[i]]
        if sub[:4] == b"NTXR":
            ntx = max(ntx, len(sub))
        elif sub[:4] == b"FHM ":
            rr = fhm_chunks(sub)
            if rr:
                for k in range(rr[0]):
                    kk = sub[rr[1][k]:rr[1][k] + rr[2][k]]
                    if kk[:4] == b"NTXR":
                        ntx = max(ntx, len(kk))
    return has_capt, ntx


def walk(data, path, langs, orphans, depth=0):
    r = fhm_chunks(data)
    if not r or depth > MAX_DEPTH:
        return
    nof, offs, sizes = r
    kids = [data[offs[i]:offs[i] + sizes[i]] for i in range(nof)]
    if 2 <= nof <= 12 and all(k[:4] == b"FHM " for k in kids):
        infos = [slot_info(k) for k in kids]
        if all(ok for ok, _ in infos):
            langs.append({
                "path": list(path),
                "md5": [hashlib.md5(k).hexdigest() for k in kids],
                "ntx": [n for _, n in infos],
            })
            return
    if any(k[:4] == b"CAPT" for k in kids):
        # A CAPT block without a language wrapper is a font package, not a subtitle
        # package -- unless its layout table actually has entries.
        used = 0
        ci = next(i for i, k in enumerate(kids) if k[:4] == b"CAPT")
        if ci + 1 < len(kids):
            tab = kids[ci + 1]
            used = sum(1 for i in range(len(tab) // 16)
                       if tab[i * 16:i * 16 + 4] != b"\xff\xff\xff\xff")
        orphans.append({"path": list(path), "nof": nof, "sizes": sizes[:10], "used": used})
        return
    for i, k in enumerate(kids):
        if k[:4] == b"FHM ":
            walk(k, path + (i,), langs, orphans, depth + 1)


def scan(pacs, rows, label, verbose=False):
    found, fails = [], []
    for idx in range(len(rows)):
        data = extract_from(pacs, rows, idx)
        if data is None:
            if rows[idx][1] != 0:
                fails.append(idx)
            continue
        if b"CAPT" not in data:
            continue
        langs, orphans = [], []
        walk(data, (), langs, orphans)
        if not langs and not orphans:
            continue
        found.append({"idx": idx, "langs": langs, "orphans": orphans})
        if verbose:
            for L in langs:
                print("[%s] #%-4d path=%-8s slots=%d ntx=%s"
                      % (label, idx, "/".join(map(str, L["path"])) or "root",
                         len(L["md5"]), L["ntx"][:4]))
            for o in orphans:
                print("[%s] #%-4d path=%-8s ORPHAN caption container nof=%d"
                      % (label, idx, "/".join(map(str, o["path"])) or "root", o["nof"]))
    return found, fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="assets folder with DATA.TBL / DATAxx.PAC")
    ap.add_argument("--iso", default=None, help="unlocalized ISO (for the baseline check)")
    ap.add_argument("--iso-tbl", default=None, help="DATA.TBL dumped from that ISO")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    g = Game(args.dir)

    print("== current build ==")
    cur, cur_fail = scan(g._pacs, g.tbl_rows, "CUR", args.verbose)
    n_cont = sum(len(p["langs"]) for p in cur)
    print("packages with caption data: %d" % len(cur))
    print("unreadable records: %d %s" % (len(cur_fail), cur_fail[:20]))

    unified, mismatched = 0, []
    for p in cur:
        for L in p["langs"]:
            if L["md5"][0] == L["md5"][1]:
                unified += 1
            else:
                mismatched.append((p["idx"], L["path"], L["md5"][:2]))
    print("language containers: %d, slot0==slot1: %d, mismatched: %d"
          % (n_cont, unified, len(mismatched)))
    for idx, path, m in mismatched[:20]:
        print("  !! #%-4d path=%-8s %s" % (idx, path, m))

    orphans = [(p["idx"], o) for p in cur for o in p["orphans"]]
    real = [(i, o) for i, o in orphans if o["used"] > 0]
    fonts = [(i, o) for i, o in orphans if o["used"] == 0]
    if fonts:
        print("font packages (CAPT present but layout table empty): %d -> %s"
              % (len(fonts), sorted({i for i, _ in fonts})))
    if real:
        print("caption containers WITHOUT a language wrapper: %d" % len(real))
        for idx, o in real[:20]:
            print("  ?? #%-4d path=%-8s entries=%d"
                  % (idx, "/".join(map(str, o["path"])) or "root", o["used"]))

    baseline_hits = []
    if args.iso and args.iso_tbl:
        print("\n== baseline (unlocalized disc) ==")
        size = os.path.getsize(args.iso)
        iso_pacs = [
            ("ISO/DATA00", IsoSlice(args.iso, ISO_DATA00, size - ISO_DATA00)),
            ("ISO/DATA01", IsoSlice(args.iso, ISO_DATA01, size - ISO_DATA01)),
        ]
        iso_rows = Game.read_rows(args.iso_tbl)
        iso, iso_fail = scan(iso_pacs, iso_rows, "ISO", False)
        stock = set()
        for p in iso:
            for L in p["langs"]:
                for i, h in enumerate(L["md5"]):
                    if L["ntx"][i] >= CJK_NTXR_MIN:
                        stock.add(h)
        print("ISO packages: %d, stock CJK slot hashes: %d, unreadable: %d"
              % (len(iso), len(stock), len(iso_fail)))
        for p in cur:
            for L in p["langs"]:
                hit = [i for i, h in enumerate(L["md5"]) if h in stock]
                if hit:
                    baseline_hits.append((p["idx"], L["path"], hit))
                    print("  !! #%-4d path=%-8s still stock JP at slot(s) %s"
                          % (p["idx"], L["path"], hit))
        print("slots still identical to stock Japanese: %d" % len(baseline_hits))

    ok = not mismatched and not baseline_hits and not real
    print("\nVERDICT:", "PASS" if ok else "REVIEW")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
