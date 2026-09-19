"""工具2 三项新需求的专项测试：合适缩放 / 图片对对称与一致 / 自导出JSON复原。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

import common_ui as cu
import tool2_refine as t2

HERE = Path(__file__).resolve().parent
DUMMY = HERE.parent / "test_data"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
STATE1 = OUT / "state1.json"
OWN = OUT / "own_state.json"

app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(t2.TOOL_QSS)


def _close_modals():
    w = QApplication.activeModalWidget()
    if isinstance(w, QMessageBox):
        w.reject()


_auto = QTimer()
_auto.timeout.connect(_close_modals)
_auto.start(80)


def pump(ms=300):
    end = datetime.now() + timedelta(milliseconds=ms)
    while datetime.now() < end:
        app.processEvents()


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def rgb_img(w=96, h=64):
    from PIL import Image
    return cu._pil_to_qimage(Image.new("RGB", (w, h), (200, 60, 60)))


def depth_img(w=64, h=96):
    from PIL import Image
    return cu._pil_to_qimage(Image.new("L", (w, h), 120))


def main():
    # ---------- 需求1：小图在放大查看时应放大到充满窗口（比例 > 1）
    pane = cu.FitImagePane()
    pane.resize(800, 600)
    pane.show()
    app.processEvents()
    pane.set_image(rgb_img(96, 64))
    app.processEvents()
    expect(pane.scale() > 1.5, f"小图未放大填充: scale={pane.scale()}")
    # 大图应缩小到窗口内（比例 < 1）
    pane.set_image(rgb_img(4000, 3000))
    app.processEvents()
    expect(pane.scale() < 1.0, f"大图未缩小适配: scale={pane.scale()}")
    print("需求1 OK: 小图放大、大图缩小，均适配窗口")

    # ---------- 需求2：图片对左右对称（共享缩放）+ 空格放大即图片对
    pv = cu.PairViewWidget()
    pv.resize(1200, 700)
    pv.show()
    app.processEvents()
    pv.set_images(rgb_img(96, 64), depth_img(64, 96), "RGB", "深度")  # 两侧长宽比不同
    app.processEvents()
    s1, s2 = pv.box_rgb.pane.scale(), pv.box_depth.pane.scale()
    expect(abs(s1 - s2) < 1e-6, f"左右缩放不对称: {s1} vs {s2}")
    # 模拟滚轮缩放一侧 → 另一侧同步
    pv.box_rgb.pane.set_scale(s1 * 1.5)
    pv._sync_zoom(pv.box_rgb, s1 * 1.5)
    expect(abs(pv.box_depth.pane.scale() - s1 * 1.5) < 1e-6, "滚轮缩放未同步到另一侧")
    print("需求2a OK: 点击放大左右对称、缩放同步")

    # 空格放大 = 图片对浏览
    expect(STATE1.exists(), "缺少 state1.json，请先跑 smoke_tool1")
    win = t2.MainWindow()
    win.resize(1500, 900)
    win.show()
    expect(win.import_json(STATE1), "导入失败")
    pump(400)
    gd = next(g for g in win.groups.values() if any(p.rgb and p.depth for p in g.pairs))
    dlg = t2.ZoomWalkDialog(win, gd, lambda pair: win._sync_group(str(gd.path), pair))
    expect(len(dlg.pairs) == len(gd.pairs), "浏览序列应为图片对列表")
    first = dlg.pairs[0]
    # 当前对应同时具备两侧图（完整对）
    expect(first.rgb is not None and first.depth is not None, "初始对应为完整图片对")
    # W 排除当前图片对 → 两侧均排除
    dlg._toggle_pair()
    expect(first.rgb in gd.exc_rgb and first.depth in gd.exc_depth, "W 未排除整对")
    # S 恢复
    dlg._restore_pair()
    expect(first.rgb not in gd.exc_rgb and first.depth not in gd.exc_depth, "S 未恢复整对")
    # 单边排除按钮
    dlg._toggle_side(False)
    expect(first.rgb in gd.exc_rgb and first.depth not in gd.exc_depth, "单边RGB排除失败")
    dlg._toggle_side(False)
    # 翻页
    n = len(dlg.pairs)
    dlg._step(1)
    expect(dlg.idx == 1 % n, "A/D 翻页失败")
    dlg.close()
    print("需求2b OK: 空格放大按图片对浏览，W/S 排除/恢复整对，单边可排除")

    # ---------- 需求3：导出 → 再导入，完整复原
    # 构造排除状态：整对排除 + 单边排除 + 整组排除（完全）
    g_list = [g for g in win.groups.values() if g.pairs]
    ga, gb = g_list[0], g_list[1]
    pa = next(p for p in ga.pairs if p.rgb and p.depth)
    win._toggle_image_excluded(str(pa.rgb), False)
    win._toggle_image_excluded(str(pa.depth), True)          # 整对排除
    pb = next(p for p in gb.pairs if p.rgb and p.depth and p is not pa)
    win._toggle_image_excluded(str(pb.rgb), False)           # 仅排除RGB
    dead = next((g for g in win.groups.values() if g is not ga and g.remaining()
                 and g.rgb), None)
    if dead is not None:
        win._set_group_excluded(str(dead.path), True)        # 整组排除
    before = win.collect_state()
    expect(win.export_to(OWN), "导出失败")
    data = json.loads(OWN.read_text(encoding="utf-8"))
    expect("全部组" in data and set(data["全部组"]) == set(win.groups), "导出未记录全部组")

    win2 = t2.MainWindow()
    win2.resize(1500, 900)
    win2.show()
    expect(win2.import_json(OWN), "自格式导入失败")
    pump(300)
    # 复原校验：每组排除集合一致（或等价：保留集合一致）
    for gp, gd0 in win.groups.items():
        expect(gp in win2.groups, f"组缺失: {gp}")
        g1 = win2.groups[gp]
        r0 = {str(p) for p in gd0.rgb_retained()}
        d0 = {str(p) for p in gd0.depth_retained()}
        r1 = {str(p) for p in g1.rgb_retained()}
        d1 = {str(p) for p in g1.depth_retained()}
        expect(r0 == r1, f"RGB保留集合不一致 {gp}: {r0 ^ r1}")
        expect(d0 == d1, f"深度保留集合不一致 {gp}: {d0 ^ d1}")
    after = win2.collect_state()
    for k in ("图片对", "仅RGB图", "仅深度图"):
        expect(set(before[k]) == set(after[k]), f"复原后 {k} 不一致")
    for k in before["统计信息"]:
        expect(before["统计信息"][k] == after["统计信息"][k],
               f"统计不一致 {k}: {before['统计信息'][k]} vs {after['统计信息'][k]}")
    print("需求3 OK: 导出→导入 完整复原（含整对/单边/整组排除状态与统计）")

    win.close()
    win2.close()
    print("ALL FEATURE TESTS PASSED")


if __name__ == "__main__":
    main()
