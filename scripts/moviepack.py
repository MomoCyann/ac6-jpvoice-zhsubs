#!/usr/bin/env python3
"""moviepack.bin 探针：列出 4 段影片，按段切出原始数据。

moviepack.bin 是一个独立文件，**不在 DATA.TBL 索引内**，4 段影片靠固定偏移定位。
每段开头是一个 ASF Header Object，但头部之后**没有 Data Object**，
而是游戏自己的偏移表（每项 4 字节：0x02 + 24 位偏移），所以 ffmpeg 打不开。

    python moviepack.py --list
    python moviepack.py --cut --out D:/tmp/movies

切出来的文件**不能直接播放**，仅用于进一步分析或将来重建 Data Object。
"""

import argparse
import os
import struct
import sys

# 四段影片的固定起始偏移（末尾一段到文件末尾）
SEGMENTS = [0, 164657152, 329302016, 512849920]
HEADER_SIZE = 5303          # 每段开头的 ASF Header Object 长度
ASF_HEADER_GUID = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")
FILE_PROPS_GUID = bytes.fromhex("a1dcab8c47a9cf118ee400c00c205365")


def read_file_props(d, base):
    """取 File Properties Object 里的时长 / 包数 / 标称文件大小。"""
    i = d.find(FILE_PROPS_GUID, base, base + 6000)
    if i < 0:
        return None
    body = d[i + 24: i + 24 + 104]
    fsz, _crt, dpk, pdur, _sdur, pre = struct.unpack("<QQQQQQ", body[16:16 + 48])
    return {
        "file_size": fsz,
        "duration": pdur / 1e7,
        "packets": dpk,
        "preroll_ms": pre,
    }


def last_nonzero(d, start, end):
    i = end - 1
    while i > start and d[i] == 0:
        i -= 1
    return i + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=r"F:\Ace Combat 6 Fires of Liberation\assets",
                    help="assets 目录，或直接给 moviepack.bin 路径")
    ap.add_argument("--list", action="store_true", help="列出四段影片参数")
    ap.add_argument("--cut", action="store_true", help="切出四段原始数据")
    ap.add_argument("--out", default="movies_out", help="--cut 的输出目录")
    args = ap.parse_args()

    path = args.dir
    if os.path.isdir(path):
        path = os.path.join(path, "moviepack.bin")

    d = open(path, "rb").read()
    print(f"moviepack: {path}")
    print(f"总大小   : {len(d):,} 字节")
    print()

    rows = []
    for k, s in enumerate(SEGMENTS):
        e = SEGMENTS[k + 1] if k + 1 < len(SEGMENTS) else len(d)
        if d[s:s + 16] != ASF_HEADER_GUID:
            print(f"seg{k}: 偏移 {s:,} 不是 ASF 头，跳过")
            continue
        info = read_file_props(d, s) or {}
        rows.append((k, s, e - s, info, e))

    print(f"{'段':<4}{'起始偏移':>14}{'槽位大小':>14}{'时长':>10}{'包数':>8}{'标称大小':>14}")
    for k, s, size, info, _e in rows:
        print(f"{k:<4}{s:>14,}{size:>14,}{info.get('duration', 0):>9.2f}s"
              f"{info.get('packets', 0):>8,}{info.get('file_size', 0):>14,}")

    durations = {round(i.get("duration", 0), 1) for _k, _s, _z, i, _e in rows}
    if len(durations) == 1:
        print(f"\n四段时长全为 {durations.pop()}s → 判定为同一部影片的多语言版本。")

    if not args.cut:
        return

    os.makedirs(args.out, exist_ok=True)
    for k, s, _size, _info, e in rows:
        end = last_nonzero(d, s, e)
        fn = os.path.join(args.out, f"seg{k}.asf")
        with open(fn, "wb") as f:
            f.write(d[s:end])
        print(f"写出 {fn}  ({end - s:,} 字节)")
    print("\n注意：这些文件无法直接播放（缺少 ASF Data Object），仅供分析。")


if __name__ == "__main__":
    sys.exit(main() or 0)
