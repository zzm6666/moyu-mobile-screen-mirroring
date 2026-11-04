# -*- coding: utf-8 -*-
"""把方形的 app_256.png 生成圆形应用图标。

产物：
- app_icon.png  256x256 圆形图标（圆外全透明，边缘抗锯齿），操作栏用它贴图
- app.ico       多尺寸 Windows 图标（托盘 / exe 资源），每一档都是圆形

圆形边缘用 8 倍超采样绘制再缩回，避免 16/20px 小尺寸下出现硬边。
"""
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'app_256.png')
OUT_PNG = os.path.join(HERE, 'app_icon.png')
OUT_ICO = os.path.join(HERE, 'app.ico')

SIZE = 256
SS = 8  # 圆边超采样倍率
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def circle_mask(size, ss=SS):
    """抗锯齿的圆形蒙版（L 模式）。"""
    big = Image.new('L', (size * ss, size * ss), 0)
    ImageDraw.Draw(big).ellipse([0, 0, size * ss - 1, size * ss - 1], fill=255)
    return big.resize((size, size), Image.LANCZOS)


def make_round(src_path, size=SIZE):
    im = Image.open(src_path).convert('RGBA')
    if im.size != (size, size):
        im = im.resize((size, size), Image.LANCZOS)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), circle_mask(size))
    return out


def main():
    if not os.path.exists(SRC):
        print('missing source: %s' % SRC)
        return 1
    big = make_round(SRC, 1024)  # 先做 1024 的圆，再逐档缩小，边缘更干净
    png = big.resize((SIZE, SIZE), Image.LANCZOS)
    png.save(OUT_PNG)
    png.save(OUT_ICO, format='ICO', sizes=[(s, s) for s in ICO_SIZES])
    print('wrote %s (%d B)' % (OUT_PNG, os.path.getsize(OUT_PNG)))
    print('wrote %s (%d B)' % (OUT_ICO, os.path.getsize(OUT_ICO)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
