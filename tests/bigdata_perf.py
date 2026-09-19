"""大数据集专项测试：扫描进度对话框、缩略图懒加载与离屏内存回收、大组分批建卡。"""
from __future__ import annotations

import os
import random
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

import common_ui as cu
import tool1_filter as t1
import tool2_refine as t2

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
BIG = OUT / "big_ds"          # 大数据集：多组
BIGGROUP = OUT / "biggroup_ds"  # 单组上千对

app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(t1.TOOL_QSS)


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


def make_big_ds(n_blocks=30, groups_per_block=8, imgs=10):
    shutil.rmtree(BIG, ignore_errors=True)
    rng = random.Random(7)
    for b in range(n_blocks):
        for g in range(groups_per_block):
            d = BIG / f"块{b:03d}" / "工件-状态" / f"{g + 1:03d}" / "color"
            d.mkdir(parents=True)
            for i in range(imgs):
                img = Image.new("RGB", (96, 64), (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)))
                img.save(d / f"{i:04d}.jpg")


def make_biggroup_ds(n_pairs=1200):
    shutil.rmtree(BIGGROUP, ignore_errors=True)
    c = BIGGROUP / "块大组" / "工件-状态" / "001" / "color"
    d = BIGGROUP / "块大组" / "工件-状态" / "001" / "depth"
    c.mkdir(parents=True)
    d.mkdir(parents=True)
    rng = random.Random(9)
    for i in range(n_pairs):
        Image.new("RGB", (96, 64), (rng.randint(0, 255), rng.randint(0, 255), 0)).save(c / f"{i:04d}.jpg")
        Image.new("L", (96, 64), rng.randint(0, 255)).save(d / f"{i:04d}.png")


def loaded_count(win, panel=None):
    strips = (panel or win).findChildren(cu.ThumbStrip)
    labels = [e["lbl"] for s in strips for e in s._entries]
    return sum(1 for l in labels if l.property("loaded")), len(labels)


def main():
    make_big_ds()
    make_biggroup_ds()
    try:
        run_checks()
    finally:
        shutil.rmtree(BIG, ignore_errors=True)
        shutil.rmtree(BIGGROUP, ignore_errors=True)
        shutil.rmtree(OUT / "state1.json", ignore_errors=True)
    print("BIG DATA ALL PASSED")


def run_checks():
    # ---------- 1. 异步扫描（进度对话框路径）+ 工具1加载大集 ----------
    w1 = t1.MainWindow()
    w1.resize(1000, 600)   # 小窗口 → 视口有限，便于验证懒加载/回收
    w1.show()
    pump(100)
    t0 = datetime.now()
    w1.load_dataset(BIG)
    for _ in range(500):
        app.processEvents()
        if not w1._build_plan and not w1._build_timer.isActive():
            break
    pump(2500)   # 让可见缩略图加载
    dt = (datetime.now() - t0).total_seconds()
    n_groups = 30 * 8
    expect(len(w1.groups) == n_groups, f"组数不符: {len(w1.groups)}")
    loaded, total = loaded_count(w1)
    print(f"1) 大数据集 {n_groups}组/{total}张缩略图，加载耗时 {dt:.1f}s，首屏加载 {loaded} 张（懒加载生效）")
    expect(0 < loaded < total, f"懒加载异常: {loaded}/{total}")

    # ---------- 2. 滚动到远处 → 顶部缩略图被回收；滚回 → 重新加载 ----------
    top_strip = None
    strips = w1.panel_mid.findChildren(cu.ThumbStrip)
    for s in strips:
        if s.isVisible() and any(e["lbl"].property("loaded") for e in s._entries):
            top_strip = s
            break
    expect(top_strip is not None, "无已加载条")
    sb = w1.panel_mid.scroll.verticalScrollBar()
    sb.setValue(sb.maximum())   # 滚到底
    pump(1500)
    top_loaded = sum(1 for e in top_strip._entries if e["lbl"].property("loaded"))
    print(f"2) 滚动到远端后，原顶部条已加载张数: {top_loaded}（离屏回收生效）")
    expect(top_loaded == 0, f"离屏未回收: {top_loaded}")
    sb.setValue(0)   # 滚回顶部
    pump(2000)
    back_loaded = sum(1 for e in top_strip._entries if e["lbl"].property("loaded"))
    expect(back_loaded > 0, "滚回后未重新加载")
    print(f"   滚回顶部后重新加载 {back_loaded} 张")
    w1.close()

    # ---------- 3. 工具2 单组上千对：分批建卡 + 懒加载 ----------
    app.setStyleSheet(t2.TOOL_QSS)
    w2 = t2.MainWindow()
    w2.resize(1200, 700)
    w2.show()
    t0 = datetime.now()
    w2.load_dataset(BIGGROUP)
    pump(500)
    gp = str(next(iter(w2.groups)))
    w2._select_group_item(gp)
    # 选中瞬间只有少量卡片（分批），随后渐进补全
    early = w2.ws_lay.count()
    pump(10000)
    full = w2.ws_lay.count()
    dt = (datetime.now() - t0).total_seconds()
    print(f"3) 单组1200对：选中即刻 {early} 卡 → {full} 卡（分批建卡），全程 {dt:.1f}s")
    expect(full == 1200, f"卡片数不符: {full}")
    loaded2, total2 = loaded_count(w2)
    print(f"   缩略图懒加载: {loaded2}/{total2}")
    expect(0 < loaded2 < total2, f"懒加载异常: {loaded2}/{total2}")
    # 状态与导出正确性抽查
    st = w2.collect_state()
    expect(len(st["图片对"]) == 1200 and st["统计信息"]["最终保留的总RGB图数"] == 1200, "大组统计不符")
    w2.close()
    print("BIG DATA CHECKS OK")


if __name__ == "__main__":
    main()
