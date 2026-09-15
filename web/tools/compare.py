#!/usr/bin/env python3
"""시안 PNG와 구현 스크린샷을 대조한다.

핵심 아이디어: 구현을 시안과 "같은 픽셀 크기"로 찍으면 글자 잉크 높이를 직접 비교할 수 있다.
폰트 크기를 잉크/em 비율로 역산할 필요가 없어져 대조가 정확해진다.

사용:
  python3 tools/compare.py <시안.png> <구현.png>                 세로 밴드 대조
  python3 tools/compare.py <시안.png> <구현.png> --detail        밴드마다 x덩어리(요소)까지 대조
  옵션 --anchor topbar|width  (기본 topbar — 상단바 괘선을 1:1로 맞춤)

읽는 법:
  Δy0  : 요소가 시안보다 얼마나 아래(+)/위(-)에 있는가  → 간격 문제
  Δink : 글자 잉크 높이 차이. +면 우리 글자가 더 크다     → 글자 크기 문제
  Δx0  : 가로 시작 위치 차이                              → 정렬·여백 문제
  Δw   : 요소 폭 차이                                     → 크기·자간 문제
"""
import sys
import numpy as np
from PIL import Image

THR = 190


def load(path):
    return np.asarray(Image.open(path).convert("L"))


def bands(g, thr=THR):
    ink = (g < thr).sum(axis=1)
    out, st = [], None
    for i, s in enumerate(ink > 0):
        if s and st is None:
            st = i
        elif not s and st is not None:
            out.append((st, i))
            st = None
    if st is not None:
        out.append((st, len(ink)))
    return out


def clusters(g, y0, y1, gap=14, thr=THR):
    """밴드 안의 가로 덩어리(= 개별 요소)와 각각의 잉크 높이."""
    b = g[y0:y1] < thr
    col = b.sum(axis=0)
    out, st, last = [], None, None
    for x, c in enumerate(col > 0):
        if c:
            if st is None:
                st = x
            last = x
        elif st is not None and x - last > gap:
            out.append((st, last))
            st = None
    if st is not None:
        out.append((st, last))
    res = []
    for s, e in out:
        sub = b[:, s : e + 1]
        ys = np.where(sub.sum(axis=1) > 0)[0]
        res.append({"x0": s, "x1": e, "w": e - s + 1, "ink": int(ys[-1] - ys[0] + 1), "y0": y0 + int(ys[0])})
    return res


def topbar_rule(g):
    H, W = g.shape
    for y in range(20, min(150, H)):
        if (g[y] < 243).sum() > W * 0.8:
            return y
    return None


def main():
    a = sys.argv[1:]
    ref_p, got_p = a[0], a[1]
    detail = "--detail" in a
    anchor = a[a.index("--anchor") + 1] if "--anchor" in a else "topbar"

    R, G = load(ref_p), load(got_p)
    rH, rW = R.shape
    gH, gW = G.shape

    if anchor == "topbar":
        rt, gt = topbar_rule(R), topbar_rule(G)
        scale = (rt / gt) if (rt and gt) else (rW / gW)
        print(f"# 기준=상단바  시안 {rW}x{rH}(rule {rt})  구현 {gW}x{gH}(rule {gt})  scale={scale:.4f}")
    else:
        scale = rW / gW
        print(f"# 기준=가로폭  시안 {rW}x{rH}  구현 {gW}x{gH}  scale={scale:.4f}")

    rB, gB = bands(R), bands(G)
    gn = [(s * scale, e * scale, i) for i, (s, e) in enumerate(gB)]

    print(f"\n## 밴드 대조 — 시안 {len(rB)} / 구현 {len(gn)}")
    print(f"{'시안 y':>14s} {'구현 y':>14s} {'Δy0':>6s} {'Δh':>6s}   요소")
    used = set()
    pairs = []
    for s, e in rB:
        best, bd = None, 1e9
        for gs, ge, j in gn:
            if j in used:
                continue
            d = abs(gs - s)
            if d < bd:
                bd, best = d, (gs, ge, j)
        if best is None or bd > max(44, (e - s) * 3):
            print(f"{s:6.0f}-{e:<7.0f} {'— 누락':>14s}")
            continue
        gs, ge, j = best
        used.add(j)
        pairs.append(((s, e), (gB[j][0], gB[j][1]), (gs, ge)))
        print(f"{s:6.0f}-{e:<7.0f} {gs:6.0f}-{ge:<7.0f} {gs-s:+6.0f} {(ge-gs)-(e-s):+6.0f}")

    extra = [(gs, ge) for gs, ge, j in gn if j not in used]
    if extra:
        print(f"\n## 시안에 짝 없는 구현 밴드 {len(extra)}개: " + ", ".join(f"{a:.0f}-{b:.0f}" for a, b in extra[:12]))

    if not detail:
        return

    print("\n## 요소 대조 (잉크 높이 Δink 가 글자 크기 차이다)")
    for (rs, re_), (gs_raw, ge_raw), _ in pairs:
        rc = clusters(R, rs, re_)
        gc = clusters(G, gs_raw, ge_raw)
        if not rc:
            continue
        print(f"\n  밴드 시안 y{rs}-{re_}  (요소 시안 {len(rc)} / 구현 {len(gc)})")
        gcn = [
            {"x0": c["x0"] * scale, "w": c["w"] * scale, "ink": c["ink"] * scale} for c in gc
        ]
        taken = set()
        for c in rc:
            best, bd = None, 1e9
            for k, d in enumerate(gcn):
                if k in taken:
                    continue
                dist = abs(d["x0"] - c["x0"])
                if dist < bd:
                    bd, best = dist, k
            if best is None or bd > 160:
                print(f"    시안 x{c['x0']:4d} w{c['w']:4d} ink{c['ink']:3d}  →  누락")
                continue
            taken.add(best)
            d = gcn[best]
            print(
                f"    시안 x{c['x0']:4d} w{c['w']:4d} ink{c['ink']:3d}  →  "
                f"Δx0{d['x0']-c['x0']:+5.0f} Δw{d['w']-c['w']:+5.0f} Δink{d['ink']-c['ink']:+4.0f}"
            )


if __name__ == "__main__":
    main()
