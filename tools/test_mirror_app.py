# -*- coding: utf-8 -*-
"""几何 / 缩放 / 定位的纯函数单元测试。

只测纯逻辑，不碰窗口、不碰 scrcpy。用 importlib 按路径加载 mirror_app.py。
"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'mirror_app.py')

m = None


def setUpModule():
    global m
    spec = importlib.util.spec_from_file_location('mirror_app_under_test', SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)


def video_of(l, t, r, b):
    """从外框矩形推出视频区尺寸（与 calc_video_rect 的 FRAME/TOOLBAR_H 约定一致）。"""
    return r - l - 2 * m.FRAME, b - t - m.TOOLBAR_H - m.FRAME


class TestCalcVideoRect(unittest.TestCase):
    """calc_video_rect：给定外框 W/H 与手机宽高比，输出视频洞 (vx,vy,vw,vh)。"""

    def test_portrait_fits_width(self):
        m.state['aspect'] = 1080 / 2376
        vx, vy, vw, vh = m.calc_video_rect(260, 610)
        self.assertEqual(vx, 0)
        self.assertEqual(vy, m.TOOLBAR_H)
        self.assertEqual(vw, 260)
        self.assertAlmostEqual(vw / vh, 1080 / 2376, delta=0.01)

    def test_landscape_fits_height(self):
        """横屏且外框偏矮时，视频高度占满、左右留白，比例不变。"""
        m.state['aspect'] = 2376 / 1080
        vx, vy, vw, vh = m.calc_video_rect(700, 300)
        self.assertEqual(vh, 300 - m.TOOLBAR_H)
        self.assertAlmostEqual(vw / vh, 2376 / 1080, delta=0.01)
        self.assertGreaterEqual(vx, 0)

    def test_landscape_fits_width(self):
        """横屏且外框够宽时，视频宽度占满。"""
        m.state['aspect'] = 2376 / 1080
        vx, vy, vw, vh = m.calc_video_rect(620, 620)
        self.assertEqual(vx, 0)
        self.assertEqual(vw, 620)
        self.assertAlmostEqual(vw / vh, 2376 / 1080, delta=0.01)

    def test_square_window_crops_vertically(self):
        """外框偏方时，视频按比例缩到能放下，多出的空间留白而不是拉伸。"""
        m.state['aspect'] = 0.5
        vx, vy, vw, vh = m.calc_video_rect(400, 300)
        self.assertLessEqual(vw, 400)
        self.assertLessEqual(vh, 300 - m.TOOLBAR_H)
        self.assertAlmostEqual(vw / vh, 0.5, delta=0.01)
        self.assertGreaterEqual(vx, 0)
        self.assertGreaterEqual(vy, m.TOOLBAR_H)

    def test_degenerate_size_returns_zero(self):
        m.state['aspect'] = 0.5
        self.assertEqual(m.calc_video_rect(10, 10), (0, 0, 0, 0))


class TestFrameSizeForAspect(unittest.TestCase):
    """frame_size_for_aspect：长边固定 VIDEO_LONG，短边按比例，横竖屏都无黑边。"""

    def test_portrait_uses_fixed_height(self):
        fw, fh, vw, vh = m.frame_size_for_aspect(1080 / 2376)
        self.assertEqual(vh, m.VIDEO_LONG)
        self.assertAlmostEqual(vw / vh, 1080 / 2376, delta=0.01)
        self.assertEqual(fw, vw + 2 * m.FRAME)
        self.assertEqual(fh, vh + m.TOOLBAR_H + m.FRAME)

    def test_landscape_uses_fixed_width(self):
        fw, fh, vw, vh = m.frame_size_for_aspect(2376 / 1080)
        self.assertEqual(vw, m.VIDEO_LONG)
        self.assertAlmostEqual(vw / vh, 2376 / 1080, delta=0.01)

    def test_extreme_aspect_keeps_min_size(self):
        fw, fh, vw, vh = m.frame_size_for_aspect(20.0)
        self.assertGreaterEqual(vh, 40)
        fw, fh, vw, vh = m.frame_size_for_aspect(0.01)
        self.assertGreaterEqual(vw, 40)


class TestClampIntoWorkarea(unittest.TestCase):
    """clamp_into_workarea：窗口不许漏到工作区外（需求4/5 的防出屏底线）。"""

    def test_inside_is_untouched(self):
        self.assertEqual(m.clamp_into_workarea(100, 100, 260, 610, 1920, 1040, 16),
                         (100, 100))

    def test_pushed_back_from_right_bottom(self):
        x, y = m.clamp_into_workarea(1900, 1000, 260, 610, 1920, 1040, 16)
        self.assertEqual(x, 1920 - 260 - 16)
        self.assertEqual(y, 1040 - 610 - 16)

    def test_negative_is_pushed_back(self):
        self.assertEqual(m.clamp_into_workarea(-50, -50, 260, 610, 1920, 1040, 16),
                         (16, 16))

    def test_oversized_window_sticks_to_margin(self):
        x, y = m.clamp_into_workarea(0, 0, 3000, 2000, 1920, 1040, 16)
        self.assertEqual((x, y), (16, 16))


class TestLockAspect(unittest.TestCase):
    """lock_aspect：拖动任一方向缩放，视频区都保持手机宽高比、被拖边不动。"""

    ASPECT = 1080 / 2376

    def test_right_edge_drives_height(self):
        """拖右边：左边界与上边界不动，高随宽重算。"""
        l, t, r, b = m.lock_aspect(100, 100, 400, 700, 11, self.ASPECT)
        self.assertEqual(l, 100)
        self.assertEqual(t, 100)
        vw, vh = video_of(l, t, r, b)
        self.assertAlmostEqual(vw / vh, self.ASPECT, delta=0.01)

    def test_bottom_edge_drives_width(self):
        """拖下边：上边界与左边界不动，宽随高重算。"""
        l, t, r, b = m.lock_aspect(100, 100, 360, 900, 15, self.ASPECT)
        self.assertEqual(l, 100)
        self.assertEqual(t, 100)
        vw, vh = video_of(l, t, r, b)
        self.assertAlmostEqual(vw / vh, self.ASPECT, delta=0.01)

    def test_top_left_corner_anchors_bottom_right(self):
        """拖左上角：右下角钉死不动。"""
        l, t, r, b = m.lock_aspect(50, 60, 400, 700, 13, self.ASPECT)
        self.assertEqual(r, 400)
        self.assertEqual(b, 700)
        vw, vh = video_of(l, t, r, b)
        self.assertAlmostEqual(vw / vh, self.ASPECT, delta=0.01)

    def test_bottom_right_corner_anchors_top_left(self):
        l, t, r, b = m.lock_aspect(100, 100, 500, 900, 17, self.ASPECT)
        self.assertEqual(l, 100)
        self.assertEqual(t, 100)
        vw, vh = video_of(l, t, r, b)
        self.assertAlmostEqual(vw / vh, self.ASPECT, delta=0.01)

    def test_never_shrinks_below_min_video(self):
        l, t, r, b = m.lock_aspect(100, 100, 101, 101, 17, self.ASPECT)
        vw, vh = video_of(l, t, r, b)
        self.assertGreaterEqual(vh, m.MIN_VIDEO)
        self.assertAlmostEqual(vw / vh, self.ASPECT, delta=0.02)

    def test_landscape_aspect(self):
        l, t, r, b = m.lock_aspect(0, 0, 600, 400, 11, 2376 / 1080)
        vw, vh = video_of(l, t, r, b)
        self.assertAlmostEqual(vw / vh, 2376 / 1080, delta=0.01)

    def test_aspect_is_stable_across_many_steps(self):
        """连续多步缩放不累积误差（每步都以当前矩形重新吸附）。"""
        l, t, r, b = 100, 100, 360, 700
        for step in range(1, 20):
            l, t, r, b = m.lock_aspect(l, t, r + step * 7, b + step * 3, 17, self.ASPECT)
            vw, vh = video_of(l, t, r, b)
            self.assertAlmostEqual(vw / vh, self.ASPECT, delta=0.01)


class TestClampRadius(unittest.TestCase):
    """clamp_radius：半径不许超过短边一半。"""

    def test_normal(self):
        self.assertEqual(m.clamp_radius(260, 610, 10), 10)

    def test_clamped_to_half_short_side(self):
        self.assertEqual(m.clamp_radius(12, 600, 10), 6)

    def test_non_positive(self):
        self.assertEqual(m.clamp_radius(0, 100, 10), 0)
        self.assertEqual(m.clamp_radius(100, 100, -5), 0)


class TestRectEdgeHit(unittest.TestCase):
    """rect_edge_hit：屏幕点落在窗口外框的哪条抓边上（0 表示不是抓边）。"""

    def setUp(self):
        self.left, self.top, self.right, self.bottom = 1000, 400, 1260, 1010

    def hit(self, x, y, grab=6):
        r = m.RECT()
        r.left, r.top, r.right, r.bottom = self.left, self.top, self.right, self.bottom
        return m.rect_edge_hit(r, x, y, grab)

    def test_outside_returns_zero(self):
        self.assertEqual(self.hit(999, 700), 0)
        self.assertEqual(self.hit(1260, 700), 0)
        self.assertEqual(self.hit(1100, 399), 0)
        self.assertEqual(self.hit(1100, 1010), 0)

    def test_left_and_right(self):
        self.assertEqual(self.hit(1001, 700), 10)
        self.assertEqual(self.hit(1258, 700), 11)

    def test_top_and_bottom(self):
        self.assertEqual(self.hit(1100, 401), 12)
        self.assertEqual(self.hit(1100, 1008), 15)

    def test_corners(self):
        self.assertEqual(self.hit(1001, 401), 13)
        self.assertEqual(self.hit(1258, 401), 14)
        self.assertEqual(self.hit(1001, 1008), 16)
        self.assertEqual(self.hit(1258, 1008), 17)

    def test_middle_returns_zero(self):
        self.assertEqual(self.hit(1130, 700), 0)

    def test_custom_grab(self):
        self.assertEqual(self.hit(1010, 700, grab=4), 0)
        self.assertEqual(self.hit(1010, 700, grab=12), 10)
        self.assertEqual(m.rect_edge_hit(None, 0, 0), 0)


class TestEdgeHit(unittest.TestCase):
    """edge_hit：操作栏那一条整块只给拖动，整条都不算抓边。"""

    left, top, right, bottom = 1000, 400, 1260, 1010

    def hit(self, x, y, grab=6):
        r = m.RECT()
        r.left, r.top, r.right, r.bottom = self.left, self.top, self.right, self.bottom
        return m.edge_hit(r, x, y, grab)

    def test_toolbar_band_never_resizes(self):
        # 操作栏高 38：从顶边到倒数一像素都属于拖动区
        self.assertEqual(self.hit(1001, 401), 0)
        self.assertEqual(self.hit(1258, 401), 0)
        self.assertEqual(self.hit(1001, 430), 0)
        self.assertEqual(self.hit(1001, 437), 0)

    def test_below_toolbar_resizes(self):
        self.assertEqual(self.hit(1001, 438), 10)
        self.assertEqual(self.hit(1258, 438), 11)
        self.assertEqual(self.hit(1001, 1008), 16)
        self.assertEqual(self.hit(1258, 1008), 17)

    def test_bottom_and_middle(self):
        self.assertEqual(self.hit(1100, 1008), 15)
        self.assertEqual(self.hit(1100, 700), 0)
        self.assertEqual(m.edge_hit(None, 0, 0), 0)


class TestPressRaise(unittest.TestCase):
    """按住操作栏不要请求置前：那会触发 TOPMOST→NOTOPMOST 的 Z 序重排，拖动一开始就闪。"""

    def test_toolbar_press_does_not_raise(self):
        self.assertFalse(m.press_should_raise(0))
        self.assertFalse(m.press_should_raise(m.TOOLBAR_H - 1))

    def test_video_area_press_raises(self):
        self.assertTrue(m.press_should_raise(m.TOOLBAR_H))
        self.assertTrue(m.press_should_raise(m.TOOLBAR_H + 200))


class TestGlassBar(unittest.TestCase):
    """build_frame_image：操作栏是纯「白色半透明」——不截屏、不模糊，直接透出桌面。"""

    W, H, TH = 259, 610, 38

    def render(self, rgb):
        from PIL import Image, ImageStat
        m.state['close_hover'] = False
        # 用一块纯色当作桌面：操作栏的半透明像素直接叠在它上面
        bg = Image.new('RGBA', (self.W, self.H), rgb + (255,))
        out = m.build_frame_image(self.W, self.H, 0, self.TH, self.W,
                                  self.H - self.TH)
        self.assertEqual(out.size, (self.W, self.H))
        self.assertEqual(out.mode, 'RGBA')
        bg.alpha_composite(out)
        # 只取纯背景区（避开图标/标题/关闭 ×）
        bar = bg.crop((60, 2, self.W - 60, self.TH - 2)).convert('RGB')
        return ImageStat.Stat(bar)

    def test_no_backdrop_capture_any_dependency(self):
        """操作栏不能依赖截屏：截屏必须先清空窗口，那一下就是「消失又出现」。"""
        self.assertFalse(hasattr(m, 'capture_blur'))
        self.assertFalse(hasattr(m, 'blank_layered'))

    def test_bar_is_really_translucent(self):
        """仍是半透明，但透明度要足够高（文字优先），不是全不透明。"""
        m.state['close_hover'] = False
        out = m.build_frame_image(self.W, self.H, 0, self.TH, self.W, self.H - self.TH)
        a = out.crop((90, 10, self.W - 70, 11)).getchannel('A')
        self.assertGreaterEqual(a.getextrema()[1], 200)
        self.assertLess(a.getextrema()[1], 255)

    def test_bar_has_no_vertical_gradient(self):
        """操作栏是纯色白，不允许竖向渐变（用户明确不要渐变）。"""
        from PIL import Image
        m.state['close_hover'] = False
        bg = Image.new('RGBA', (self.W, self.H), (120, 120, 120, 255))
        out = m.build_frame_image(self.W, self.H, 0, self.TH, self.W, self.H - self.TH)
        bg.alpha_composite(out)
        top = sum(bg.crop((90, 4, self.W - 70, 5)).convert('RGB').getpixel((0, 0)))
        bottom = sum(bg.crop((90, 32, self.W - 70, 33)).convert('RGB').getpixel((0, 0)))
        self.assertLessEqual(abs(top - bottom), 3)

    def test_dark_backdrop_still_white(self):
        """暗底也必须读作白色：提高不透明度后，背后深色最多只让条子略沉。"""
        dark_bar = sum(self.render((20, 20, 20)).mean) / 3
        self.assertGreater(dark_bar, 210)  # 原始底色只有 20，必须被白色压上去

    def test_text_contrast_on_dark_backdrop(self):
        """文字清晰度：暗底上标题文字必须比条子底色暗出足够多（对比度）。"""
        from PIL import Image
        m.state['close_hover'] = False
        bg = Image.new('RGBA', (self.W, self.H), (20, 20, 20, 255))
        out = m.build_frame_image(self.W, self.H, 0, self.TH, self.W, self.H - self.TH)
        bg.alpha_composite(out)
        title = bg.crop((38, 10, 70, 26)).convert('L')
        bar = bg.crop((90, 10, self.W - 70, 26)).convert('L')
        self.assertGreater(bar.getextrema()[0] - title.getextrema()[0], 100)

    def test_light_backdrop_glass_is_bright(self):
        self.assertGreater(sum(self.render((250, 250, 250)).mean) / 3, 150)

    def test_glass_stays_translucent(self):
        """半透明：暗底上仍能看出背景色透上来（不是纯白实心），中间调应明显低于纯白 tint。"""
        dark_bar = sum(self.render((20, 20, 20)).mean) / 3
        self.assertLess(dark_bar, 235)

    def test_flat_backdrop_stays_clean(self):
        """纯色背景上玻璃必须干净：不加噪点/颗粒（用户明确嫌脏），同一行内几乎无起伏。"""
        from PIL import Image, ImageStat
        m.state['close_hover'] = False
        bg = Image.new('RGBA', (self.W, self.H), (120, 120, 120, 255))
        out = m.build_frame_image(self.W, self.H, 0, self.TH, self.W,
                                  self.H - self.TH)
        bg.alpha_composite(out)
        row = bg.crop((60, 18, self.W - 60, 19)).convert('RGB')
        self.assertLess(max(ImageStat.Stat(row).stddev), 1.5)


class TestCloseButton(unittest.TestCase):
    """关闭 ×：比标题更轻——颜色浅、尺寸小，白条上不抢注意力。"""

    W, H, TH = 259, 610, 38

    def region(self, hover=False):
        m.state['close_hover'] = hover
        out = m.build_frame_image(self.W, self.H, 0, self.TH, self.W, self.H - self.TH)
        return out.crop((self.W - m.CLOSE_W, 0, self.W, self.TH)).convert('L')

    def test_cross_is_light(self):
        """颜色浅：× 最深的像素也远浅于文字色（56）。"""
        self.assertGreaterEqual(self.region().getextrema()[0], 130)

    def test_cross_is_small(self):
        """尺寸小：× 覆盖的像素占关闭区不到 6%。"""
        px = list(self.region().getdata())
        dark = sum(1 for v in px if v < 200)
        self.assertLess(dark, len(px) * 0.06)

    def test_hover_still_darkens(self):
        self.assertLess(self.region(True).getextrema()[0], self.region(False).getextrema()[0])


class TestScrcpyFlags(unittest.TestCase):
    """scrcpy 命令行必须带上这两条，否则右/下会闪出黑边。

    - 关掉窗口长宽比锁定：不然它会把 overscan 后的尺寸改回精确比例，拖动时来回打架；
    - render-fit=stretched：画面按窗口铺满，不再居中留 1px 黑底。
    """

    def test_fit_and_lock_flags(self):
        src = open(m.__file__, encoding='utf-8').read()
        self.assertIn("'--render-fit=stretched'", src)
        self.assertIn("'--no-window-aspect-ratio-lock'", src)


class TestDragPath(unittest.TestCase):
    """拖动快路径（WM_MOVING）必须用 child_box 摆视频窗口。

    直接摆 calc_video_rect() 的裸尺寸会让视频窗口在拖动时于 overscan 尺寸和裸尺寸之间
    来回跳，画面会抖、右/下会闪出黑边。
    """

    def test_moving_uses_child_box(self):
        src = open(m.__file__, encoding='utf-8').read()
        i = src.index('if msg == 0x0216')
        j = src.index('return user32.DefWindowProcW', i)
        self.assertIn('child_box(', src[i:j])


class TestPickSerial(unittest.TestCase):
    """pick_serial：配置串号在线就用它，不在线自动退回在线设备（无线调试端口会变）。"""

    CFG = '192.168.137.198:38493'

    def test_config_serial_online(self):
        self.assertEqual(m.pick_serial(self.CFG, ['emulator-5554', self.CFG]), self.CFG)

    def test_config_serial_offline_falls_back(self):
        self.assertEqual(m.pick_serial(self.CFG, ['adb-abc._adb-tls-connect._tcp']),
                         'adb-abc._adb-tls-connect._tcp')

    def test_no_device_keeps_config(self):
        self.assertEqual(m.pick_serial(self.CFG, []), self.CFG)
        self.assertEqual(m.pick_serial('', []), '')


class TestAdoptAspect(unittest.TestCase):
    """adopt_aspect：只有手机真的转了方向才改宽高比。

    视频窗口尺寸是按 aspect 摆出来的，等它出现后再量就是量到自己摆的值；无条件采纳
    会让 aspect 被取整误差污染（0.4545 → 0.4528），视频区与真实画面差 1px，scrcpy 就
    在自己窗口上下补黑边——操作栏下面那条黑线就是这么来的。
    """

    def test_tiny_rounding_ignored(self):
        self.assertAlmostEqual(m.adopt_aspect(1080 / 2376, 259 / 572), 1080 / 2376)

    def test_rotation_adopted(self):
        self.assertAlmostEqual(m.adopt_aspect(1080 / 2376, 2376 / 1080), 2376 / 1080)

    def test_slow_drift_rejected(self):
        """横屏时每秒量到的值会差 1% 左右：不能采纳，否则 aspect 一路漂到 2.0、窗口变横屏。"""
        self.assertAlmostEqual(m.adopt_aspect(2.2331, 2.2119), 2.2331)
        self.assertAlmostEqual(m.adopt_aspect(0.4545, 0.4528), 0.4545)

    def test_empty_measured_keeps_current(self):
        self.assertAlmostEqual(m.adopt_aspect(0.5, 0), 0.5)
        self.assertAlmostEqual(m.adopt_aspect(0.5, None), 0.5)

    def test_empty_current_takes_measured(self):
        self.assertAlmostEqual(m.adopt_aspect(None, 0.5), 0.5)


class TestVideoAreaClear(unittest.TestCase):
    """视频区只画接缝那 1px 白线，其余全透明（画面由 scrcpy 铺满，含 overscan）。"""

    W, H, TH = 259, 610, 38

    def image(self):
        return m.build_frame_image(self.W, self.H, 0, self.TH, self.W, self.H - self.TH)

    def test_seam_is_pure_white(self):
        """白线盖在视频窗口第一行上（那一行带深色描边），用户要纯白不透明。"""
        r, g, b, a = self.image().getpixel((self.W // 2, self.TH - m.OVERSCAN))
        self.assertEqual((r, g, b), (255, 255, 255))
        self.assertEqual(a, 255)

    def test_seam_line_is_only_one_pixel_thick(self):
        """白线只有 1px：上下两行都不能是纯白，否则看起来像粗线。"""
        out = self.image()
        y = self.TH - m.OVERSCAN
        self.assertEqual(out.getpixel((self.W // 2, y - 1))[3], m.BAR_TINT[3])
        self.assertEqual(out.getpixel((self.W // 2, y))[3], 255)
        self.assertEqual(out.getpixel((self.W // 2, y + 1))[3], m.BAR_TINT[3])

    def test_first_video_row_not_covered(self):
        """白线不能压到画面：视频区第一行必须全透明。"""
        self.assertEqual(self.image().getpixel((self.W // 2, self.TH))[3], 0)

    def test_seam_reaches_right_edge(self):
        """白线要贯穿整个宽度，不能被右边缘补丁切断。"""
        self.assertEqual(
            self.image().getpixel((self.W - 1, self.TH - m.OVERSCAN))[3], 255)

    def test_right_column_stays_transparent(self):
        """右边缘不再补色：画面靠 overscan 铺到边，补一条半透明白反而成灰边。"""
        self.assertEqual(self.image().getpixel((self.W - 1, self.TH + 50))[3], 0)

    def test_overscan_child_box(self):
        """视频窗口比可见区大 2*overscan 并向上左外扩，把底色/系统边框挤出可见区。"""
        self.assertEqual(m.OVERSCAN, 2)
        self.assertEqual(m.child_box(0, 38, 259, 572), (-2, 36, 263, 576))
        self.assertEqual(m.child_box(0, 38, 259, 572, overscan=0), (0, 38, 259, 572))
        self.assertEqual(m.child_box(10, 40, 100, 200, overscan=1), (9, 39, 102, 202))

    def test_bottom_corner_stays_clear(self):
        """底部圆角区域不能画，否则会在圆角外冒出直角。"""
        self.assertEqual(self.image().getpixel((self.W - 1, self.H - 2))[3], 0)

    def test_rest_of_video_area_transparent(self):
        out = self.image()
        for y in range(self.TH + 1, self.H):
            self.assertEqual(out.getpixel((self.W // 2, y))[3], 0)


class TestNoSystemChrome(unittest.TestCase):
    """Windows 11 的系统圆角/系统边框必须关掉。

    那条「操作栏下面的黑线」就是 Win11 给顶层窗口画的 1px 边框（DWMWA_BORDER_COLOR=34），
    视频窗口是独立顶层窗口，它上边的边框正好压在接缝上。
    """

    def test_constants(self):
        self.assertEqual(m.DWM_ROUND, 33)
        self.assertEqual(m.DWM_BORDER, 34)
        self.assertEqual(m.DWM_COLOR_NONE, 0xFFFFFFFE)

    def test_none_hwnd_is_noop(self):
        self.assertFalse(m.no_system_chrome(0))

    def test_both_windows_get_it(self):
        """视频窗口（接缝）和外框（整窗边缘）都要调。"""
        src = open(m.__file__, encoding='utf-8').read()
        self.assertEqual(src.count('no_system_chrome('), 3)  # 定义 1 + 调用 2


if __name__ == '__main__':
    unittest.main(verbosity=2)
