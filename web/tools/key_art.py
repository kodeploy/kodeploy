"""시안 일러스트에서 체크무늬 배경을 키잉해 투명 PNG로 뽑는다.

원본은 design/(git에 없음)에 있고, 결과만 web/src/assets/에 커밋한다.
사용: cd web && python3 tools/key_art.py


배경은 체크무늬가 밝은 회색(195-205)과 어두운 회색(125-140) 두 톤이라 문턱이 낮아야 한다.
잉크는 진짜 검정(0-15)이므로 문턱 100이면 체크무늬가 배경으로 떨어진다.
바깥은 테두리에서 flood fill로 찾아 투명하게, 도형 안쪽(흰 종이면)은 그대로 둔다.
"""
from PIL import Image, ImageDraw, ImageFilter
import os

def key_out(path, out, max_w):
    im = Image.open(path).convert("L")
    w, h = im.size
    binm = im.point(lambda v: 255 if v >= 100 else 0)
    binm = binm.filter(ImageFilter.MedianFilter(3))   # 체크무늬 잡티 제거
    flood = binm.copy()
    for corner in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if flood.getpixel(corner) == 255:
            ImageDraw.floodfill(flood, corner, 128, thresh=0)
    outside = flood.point(lambda v: 255 if v == 128 else 0)
    alpha = outside.point(lambda v: 0 if v else 255)
    # 잉크는 원본 톤, 도형 안쪽은 흰 종이로 (체크무늬가 비쳐 보이지 않게)
    ink = im.point(lambda v: v if v < 100 else 255)
    rgba = Image.merge("RGBA", (ink, ink, ink, alpha))
    bbox = alpha.getbbox()
    rgba = rgba.crop(bbox)
    if rgba.width > max_w:
        rgba = rgba.resize((max_w, round(rgba.height * max_w / rgba.width)), Image.LANCZOS)
    rgba.save(out, optimize=True)
    print(f"{os.path.basename(out):18} {str(rgba.size):12} {os.path.getsize(out)//1024:4}KB  crop={bbox}")

D = "design/ChatGPT Image 2026년 9월 15일 오후 "
for src, out, mw in [
    (D + "05_03_01 (1).png", "web/src/assets/flow-repo.png", 560),
    (D + "05_03_01 (3).png", "web/src/assets/flow-cube.png", 340),
    (D + "05_03_02 (5).png", "web/src/assets/flow-arrow.png", 420),
    (D + "05_03_02 (4).png", "web/src/assets/flow-app.png", 1000),
]:
    key_out(src, out, mw)
