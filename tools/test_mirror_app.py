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


if __name__ == '__main__':
    unittest.main(verbosity=2)
