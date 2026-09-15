#!/usr/bin/env python3
"""시안 PNG 계측기 — 행 밴드 / 괘선 / 밴드별 x덩어리(+색)를 뽑는다.

사용: python3 tools/measure.py <이미지> [--thr 190] [--band y0 y1] [--gap 14]
  기본        : 전체 행 밴드 + 가로/세로 괘선
  --band y0 y1: 그 구간 안의 x덩어리(단어 그룹)만 상세 출력
모든 좌표는 이미지 원본 px이다. CSS로 환산하려면 해당 렌더의 스케일로 나눈다.
"""
import sys
import numpy as np
from PIL import Image


def load(path):
    im = Image.open(path)
    return np.asarray(im.convert("L")), np.asarray(im.convert("RGB"))


def core_color(rgb, g, y0, y1, x0, x1, frac=25):
    reg = rgb[y0:y1, x0:x1].reshape(-1, 3)
    lum = g[y0:y1, x0:x1].reshape(-1)
    if len(lum) == 0:
        return (0, 0, 0)
    idx = np.argsort(lum)[: max(1, len(lum) // frac)]
    return tuple(np.median(reg[idx], axis=0).astype(int))


def runs(mask, minlen=1):
    out, st = [], None
    for i, s in enumerate(mask):
        if s and st is None:
            st = i
        elif not s and st is not None:
            if i - st >= minlen:
                out.append((st, i))
            st = None
    if st is not None:
        out.append((st, len(mask)))
    return out


def main():
    a = sys.argv[1:]
    path = a[0]
    thr = int(a[a.index("--thr") + 1]) if "--thr" in a else 190
    gap = int(a[a.index("--gap") + 1]) if "--gap" in a else 14
    g, rgb = load(path)
    H, W = g.shape
    ink = g < thr

    if "--band" in a:
        i = a.index("--band")
        y0, y1 = int(a[i + 1]), int(a[i + 2])
        col = ink[y0:y1].sum(axis=0)
        cl, st, last = [], None, None
        for x, c in enumerate(col > 0):
            if c:
                if st is None:
                    st = x
                last = x
            elif st is not None and x - last > gap:
                cl.append((st, last))
                st = None
        if st is not None:
            cl.append((st, last))
        print(f"# {path} band y{y0}-{y1} (thr={thr} gap={gap})")
        for s, e in cl:
            sub = ink[y0:y1, s : e + 1]
            ys = np.where(sub.sum(axis=1) > 0)[0]
            print(
                f"x {s:5d}-{e:5d} w={e-s+1:5d}  y {y0+ys[0]:5d}-{y0+ys[-1]:5d} h={ys[-1]-ys[0]+1:4d}  "
                f"color={core_color(rgb, g, y0, y1, s, e+1)}"
            )
        return

    print(f"# {path}  size={W}x{H}  bg={tuple(rgb[2,2])}  thr={thr}")
    print("## 행 밴드 (y0-y1 h  x0-x1  core색)")
    for s, e in runs(ink.sum(axis=1) > 0):
        xs = np.where(ink[s:e].sum(axis=0) > 0)[0]
        print(
            f"y {s:5d}-{e:5d} h={e-s:4d}  x {xs[0]:5d}-{xs[-1]:5d}  color={core_color(rgb,g,s,e,0,W)}"
        )

    print("## 가로 괘선 (폭 30% 이상, thr=243)")
    soft = g < 243
    for y in range(H):
        n = soft[y].sum()
        if n > W * 0.3:
            xs = np.where(soft[y])[0]
            print(f"y={y:5d}  폭 {n:5d}  x {xs[0]}-{xs[-1]}  색 {tuple(rgb[y, W//2])}")

    print("## 세로 괘선 (높이 20% 이상, thr=243)")
    for x in range(W):
        n = soft[:, x].sum()
        if n > H * 0.2:
            ys = np.where(soft[:, x])[0]
            print(f"x={x:5d}  높이 {n:5d}  y {ys[0]}-{ys[-1]}  색 {tuple(rgb[H//2, x])}")


if __name__ == "__main__":
    main()
