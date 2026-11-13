# -*- coding: utf-8 -*-
"""
apply.py -- migrate the Chinese (localized) subtitle/font slots into the Japanese slots
so that "Japanese voice + Chinese text" becomes readable in Ace Combat 6 (ac6_recomp).

The game has a single language switch that drives BOTH the voice pack and the text
tables. The Chinese fan patch only rewrote the English slot (index 1). Everything
below copies slot 1 into slot 0 (Japanese) so that picking Japanese in-game yields
Chinese text while the voice pack stays Japanese.

Usage
-----
    python apply.py task --dir "F:/Ace Combat 6 Fires of Liberation/assets"
    python apply.py cg   --dir "..."
    python apply.py dup  --dir "..."
    python apply.py font --dir "..."
    python apply.py all  --dir "..."

    # preview without writing anything
    python apply.py all --dir "..." --dry-run

Every run backs up DATA.TBL first and is idempotent: slots that already match the
source are skipped, so re-running does not grow the PAC files.
"""

import argparse
import hashlib
import os
import shutil
import sys
import time

from ac6lib import Game, chunk, fhm_chunks, set_chunk

# --- record ids -------------------------------------------------------------
# Mission-time cutscene packages. They carry fonts in chunk 4 (JP) / chunk 5 (EN)
# and one or more language containers in their text chunks.
TASK_IDS = list(range(9, 24))

# Chapter CG packages (ed00_01 .. ed15_01). Language container lives at c0 -> c2.
CG_IDS = [
    474, 477, 479, 482, 485, 487, 489, 493, 496,
    498, 501, 503, 505, 507, 510, 515, 521, 529,
]

# (localized original, untouched duplicate). The fan patch only edited the first
# one; the duplicate still holds the stock Japanese/English slots.
DUP_PAIRS = [(9, 103), (15, 109), (17, 111), (21, 115), (23, 117)]

JP_SLOT = 0
ZH_SLOT = 1


def md5(b):
    return hashlib.md5(b).hexdigest()


def is_lang_container(b):
    """Return slot count if `b` is FHM[FHM[CAPT..], FHM[CAPT..], ...], else 0."""
    r = fhm_chunks(b)
    if not r:
        return 0
    nof, offs, sizes = r
    if not (2 <= nof <= 12):
        return 0
    kids = [b[offs[i]:offs[i] + sizes[i]] for i in range(nof)]
    if not all(k[:4] == b"FHM " for k in kids):
        return 0
    for k in kids:
        rr = fhm_chunks(k)
        if not rr:
            return 0
        if k[rr[1][0]:rr[1][0] + 4] != b"CAPT":
            return 0
    return nof


def find_lang_containers(pkg, max_depth=3):
    """Yield (path, slot_count) for every language container inside an FHM tree."""
    def rec(d, path, depth):
        r = fhm_chunks(d)
        if not r or depth > max_depth:
            return
        n = is_lang_container(d)
        if n:
            yield path, n
            return
        nof, offs, sizes = r
        for i in range(nof):
            sub = d[offs[i]:offs[i] + sizes[i]]
            if fhm_chunks(sub):
                yield from rec(sub, path + (i,), depth + 1)

    yield from rec(pkg, (), 0)


def follow(pkg, path):
    d = pkg
    for i in path:
        d = chunk(d, i)
    return d


def unify_slots(pkg, path, src=ZH_SLOT, dsts=(JP_SLOT,), also_src=False, dry=False):
    """Copy language slot `src` into `dsts`. Returns (changed, detail)."""
    cont = follow(pkg, path)
    n = is_lang_container(cont)
    if not n:
        return False, "not a language container"
    if src >= n:
        return False, "slot %d out of range (%d)" % (src, n)
    zh = chunk(cont, src)
    zh_h = md5(zh)
    changed = False
    for d in dsts:
        if d >= n:
            continue
        if md5(chunk(cont, d)) == zh_h:
            continue
        cont = set_chunk(cont, d, zh)
        changed = True
    if also_src and md5(chunk(cont, src)) != zh_h:
        cont = set_chunk(cont, src, zh)
        changed = True
    if not changed:
        return False, "already unified"
    if dry:
        return True, "would set slot(s) %s" % (list(dsts),)
    return True, "set slot(s) %s <- slot %d (%s)" % (list(dsts), src, zh_h[:8])


def replace_node(pkg, path, new_node):
    """Set the node at `path` inside `pkg` (bottom-up rebuild)."""
    if not path:
        return new_node
    node = new_node
    for i in range(len(path) - 1, -1, -1):
        anc = follow(pkg, tuple(path[:i]))
        node = set_chunk(anc, path[i], node)
    return node


def do_task_packages(g, rows, dry, log):
    for idx in TASK_IDS:
        pkg = g.extract(idx, rows=rows)
        if pkg is None:
            log("#%d skip: unreadable" % idx)
            continue
        r = fhm_chunks(pkg)
        if not r or r[0] < 6:
            log("#%d skip: not a task package (nof=%s)" % (idx, r[0] if r else None))
            continue
        new = pkg
        notes = []

        # 1) font slot: chunk 4 (JP) <- chunk 5 (EN, patched to CJK glyphs)
        try:
            f4 = chunk(new, 4)
            f5 = chunk(new, 5)
            if fhm_chunks(f4) and fhm_chunks(f5) and md5(f4) != md5(f5):
                new = set_chunk(new, 4, f5)
                notes.append("font c4<-c5")
        except Exception as e:
            notes.append("font skipped (%s)" % e)

        # 2) every language container: slot 0 <- slot 1
        conts = list(find_lang_containers(new))
        for path, n in conts:
            sub_new = follow(new, path)
            n_cont = is_lang_container(sub_new)
            if not n_cont:
                continue
            if md5(chunk(sub_new, 0)) == md5(chunk(sub_new, 1)):
                notes.append("%s already ok" % (path,))
                continue
            sub_new = set_chunk(sub_new, 0, chunk(sub_new, 1))
            new = replace_node(new, path, sub_new)
            notes.append("%s slot0<-1" % (path,))

        if new is pkg or md5(new) == md5(pkg):
            log("#%d unchanged" % idx)
            continue
        if dry:
            log("#%d would update %s" % (idx, ", ".join(notes)))
        else:
            g.store_blob(idx, new, rows)
            log("#%d updated: %s" % (idx, ", ".join(notes)))


def do_cg_packages(g, rows, dry, log):
    for idx in CG_IDS:
        pkg = g.extract(idx, rows=rows)
        if pkg is None:
            log("#%d skip: unreadable" % idx)
            continue
        conts = list(find_lang_containers(pkg))
        if not conts:
            log("#%d skip: no language container" % idx)
            continue
        new = pkg
        touched = []
        for path, _n in conts:
            sub = follow(new, path)
            if md5(chunk(sub, 0)) == md5(chunk(sub, 1)):
                continue
            sub = set_chunk(sub, 0, chunk(sub, 1))
            new = replace_node(new, path, sub)
            touched.append(path)
        if not touched:
            log("#%d already ok" % idx)
            continue
        if dry:
            log("#%d would unify %s" % (idx, touched))
        else:
            g.store_blob(idx, new, rows)
            log("#%d updated slots at %s" % (idx, touched))


def do_duplicates(g, rows, dry, log):
    for main, dup in DUP_PAIRS:
        mpkg = g.extract(main, rows=rows)
        dpkg = g.extract(dup, rows=rows)
        if mpkg is None or dpkg is None:
            log("#%d<-#%d skip: unreadable" % (dup, main))
            continue
        new = dpkg
        touched = []
        for path, _n in list(find_lang_containers(dpkg)):
            try:
                mcont = follow(mpkg, path)
                dcont = follow(new, path)
            except Exception:
                continue
            if not is_lang_container(mcont) or not is_lang_container(dcont):
                continue
            zh = chunk(mcont, 1)
            if md5(chunk(dcont, 0)) == md5(zh) and md5(chunk(dcont, 1)) == md5(zh):
                continue
            dcont = set_chunk(dcont, 0, zh)
            dcont = set_chunk(dcont, 1, zh)
            new = replace_node(new, path, dcont)
            touched.append(path)
        if not touched:
            log("#%d<-#%d already ok" % (dup, main))
            continue
        if dry:
            log("#%d<-#%d would unify %s" % (dup, main, touched))
        else:
            g.store_blob(dup, new, rows)
            log("#%d<-#%d updated %s" % (dup, main, touched))


def main():
    ap = argparse.ArgumentParser(description="AC6 JP-voice + ZH-subtitle patcher")
    ap.add_argument("mode", choices=["task", "cg", "dup", "all", "list"])
    ap.add_argument("--dir", required=True, help="folder containing DATA.TBL / DATA00.PAC / DATA01.PAC")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    g = Game(args.dir)

    if args.mode == "list":
        for i, (fl, off, cs, us) in enumerate(g.tbl_rows):
            if off == 0:
                continue
            print("#%-4d flags=%08X off=%-12d comp=%-9d raw=%-9d pac=%s"
                  % (i, fl, off, cs, us, g.pac_for(i)))
        return

    if not args.no_backup and not args.dry_run:
        stamp = time.strftime("%y%m%d-%H%M%S")
        bak = os.path.join(args.dir, "DATA.TBL.bak-jpzh-%s" % stamp)
        shutil.copy2(g.tbl_path, bak)
        print("backup -> %s" % os.path.basename(bak))

    rows = list(g.tbl_rows)
    modes = ["task", "cg", "dup"] if args.mode == "all" else [args.mode]
    for m in modes:
        print("== %s ==" % m)
        if m == "task":
            do_task_packages(g, rows, args.dry_run, print)
        elif m == "cg":
            do_cg_packages(g, rows, args.dry_run, print)
        elif m == "dup":
            do_duplicates(g, rows, args.dry_run, print)

    if not args.dry_run:
        g.save_tbl(rows)
        print("DATA.TBL written (%d records)" % len(rows))


if __name__ == "__main__":
    sys.exit(main())
