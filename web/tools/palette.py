#!/usr/bin/env python3
"""시안 PNG 묶음에서 팔레트를 뽑는다.

면 색(가장 넓은 색들)과 신호색(채도 높은 색)을 나눠 집계한다.
사용: python3 tools/palette.py <디렉토리 또는 파일...> [--top 12]
"""
import sys, glob, os
from collections import Counter
import numpy as np
from PIL import Image


def sat(rgb):
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    mx, mn = max(r, g, b), min(r, g, b)
    return 0 if mx == 0 else (mx - mn) / mx


def main():
    argv = sys.argv[1:]
    top = 12
    if "--top" in argv:
        i = argv.index("--top")
        top = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]
    paths = []
    for a in args:
        paths += sorted(glob.glob(os.path.join(a, "**", "*.png"), recursive=True)) if os.path.isdir(a) else [a]

    surfaces, signals = Counter(), Counter()
    for p in paths:
        im = Image.open(p).convert("RGB")
        im.thumbnail((640, 640))
        a = np.asarray(im).reshape(-1, 3)
        # 8단위로 양자화해 안티에일리어싱 노이즈를 묶는다
        q = (a // 8 * 8)
        for col, n in Counter(map(tuple, q)).most_common(60):
            (signals if sat(col) > 0.25 and max(col) > 60 else surfaces)[col] += n

    def show(title, c):
        print(f"\n## {title}")
        tot = sum(c.values()) or 1
        for col, n in c.most_common(top):
            print(f"  #{col[0]:02X}{col[1]:02X}{col[2]:02X}  {n/tot*100:5.2f}%  rgb{col}")

    print(f"# {len(paths)}장")
    show("면 색 (무채색·저채도)", surfaces)
    show("신호색 (고채도)", signals)


if __name__ == "__main__":
    main()
