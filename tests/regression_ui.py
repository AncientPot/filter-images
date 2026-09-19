"""UI 回归测试：固化历次用户反馈修复的关键行为，防止复发。

覆盖：
  1. 同窗切换（ToolShell 单一窗口、首次接续、状态保留）
  2. 数据集跨页面同步（换数据集后切换不残留旧数据集）
  3. 旧数据集信息零残留（发起加载瞬间清场、通知永不含路径）
  4. 空格只放大查看未筛选区最上方组
  5. 点击缩略图与空格打开同一放大界面（定位到被点项）
  6. 顶栏布局（按钮宽度贴合、切换按钮最右、无路径/统计残留）

依赖 filtering/test_data（先运行 tests/gen_dummy_dataset.py）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

import common_ui as cu
import tool1_filter as t1
import tool2_refine as t2

HERE = Path(__file__).resolve().parent
A = HERE.parent / "filtering" / "test_data"
B = HERE / "out" / "reg_ds_b"
OUT = HERE / "out"

app = QApplication([])
app.setStyle("Fusion")


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


def make_dataset_b():
    shutil.rmtree(B, ignore_errors=True)
    d = B / "块B-回归" / "工件X-正常" / "001" / "color"
    d.mkdir(parents=True)
    Image.new("RGB", (64, 48), (10, 150, 220)).save(d / "0000.jpg")


def tool1_json(paths):
    """构造工具1格式的最小 JSON。"""
    OUT.mkdir(exist_ok=True)
    f = OUT / "reg_state1.json"
    f.write_text(json.dumps({
        "未筛选区": [], "选中区": paths, "排除区": [],
        "统计信息": {"数据集根目录绝对路径": str(A.resolve())},
    }, ensure_ascii=False), encoding="utf-8")
    return f


def main():
    make_dataset_b()
    try:
        run_checks()
    finally:
        shutil.rmtree(B, ignore_errors=True)
    print("REGRESSION UI ALL PASSED")


def run_checks():
    # ============ 1&2. 同窗切换 + 数据集同步 ============
    app.setStyleSheet(t1.TOOL_QSS)
    shell = cu.ToolShell("tool1")
    shell.resize(1600, 920)
    shell.show()
    pump(200)
    p1 = shell._pages["tool1"]
    mains = [w for w in QApplication.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible()]
    expect(len(mains) == 1 and mains[0] is shell, "应只有一个宿主窗口")

    p1.load_dataset(A)
    for _ in range(200):
        app.processEvents()
        if not p1._build_plan and not p1._build_timer.isActive():
            break
    pump(300)
    p1._move(p1._top_unfiltered(), "sel")
    p1._move(p1._top_unfiltered(), "sel")
    expect(len(p1.selected) == 2, "工具1应选中2组")

    p1._switch_to_tool2()
    pump(200)
    p2 = shell._pages["tool2"]
    expect(shell.stack.currentWidget() is p2, "应显示工具2页面")
    expect(len(p2.groups) == 2, "首次接续应载入2组")
    mains = [w for w in QApplication.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible()]
    expect(len(mains) == 1, "切换不得产生新窗口")

    # 工具1换数据集B → 切回工具2必须立即变为B
    p2._switch_to_tool1()
    pump(200)
    p1.load_dataset(B, confirm=False)
    for _ in range(200):
        app.processEvents()
        if not p1._build_plan and not p1._build_timer.isActive():
            break
    pump(200)
    p1._switch_to_tool2()
    pump(200)
    expect(str(p2.root) == str(B.resolve()), f"工具2残留旧数据集: {p2.root}")
    expect(len(p2.groups) == 1, "工具2未载入新数据集")

    # 同根来回切换保留状态
    p2._switch_to_tool1()
    pump(100)
    expect(str(p1.root) == str(B.resolve()), "工具1数据集应保持B")
    p1._switch_to_tool2()
    pump(100)
    expect(len(p2.groups) == 1, "同根切换不应重载")
    print("1-2) 同窗切换 + 数据集同步 OK")

    # ============ 3. 旧信息零残留 ============
    app.setStyleSheet(t2.TOOL_QSS)
    w2 = t2.MainWindow()
    w2.resize(1500, 900)
    w2.show()
    w2.load_dataset(A)
    pump(200)
    # 发起导入瞬间：旧通知清空、状态栏立即切换为中性提示
    seen = {}
    orig_begin = t2.MainWindow._begin_load

    def spy(self, msg):
        orig_begin(self, msg)
        seen["notify"] = self.notify_lbl.text()
        seen["status"] = self.status_lbl.text()

    t2.MainWindow._begin_load = spy
    w2.import_json(tool1_json([str(next(iter(w2.groups)))]))
    pump(300)
    t2.MainWindow._begin_load = orig_begin
    expect(seen["notify"] == "", "发起导入后旧通知未立即清除")
    expect("正在" in seen["status"], "状态栏未立即切换为中性提示")
    expect("\\" not in w2.notify_lbl.text(), "通知不得包含路径")
    print("3) 旧信息零残留 OK")

    # ============ 4&5. 空格/点击放大行为 ============
    app.setStyleSheet(t1.TOOL_QSS)
    w1 = t1.MainWindow()
    w1.resize(1680, 940)
    w1.show()
    w1.load_dataset(A)
    for _ in range(200):
        app.processEvents()
        if not w1._build_plan and not w1._build_timer.isActive():
            break
    pump(300)
    opened = {}

    class DummyZoom:
        def __init__(self, parent, group, paths, start_index=0):
            opened["group"] = str(group.path)
            opened["idx"] = start_index

        def exec(self):
            return 0

    t1.GroupZoomDialog = DummyZoom
    w1._move(w1._top_unfiltered(), "sel")
    w1.setFocus()
    app.processEvents()
    top_unf = w1._top_unfiltered()
    w1._hk_zoom()
    expect(opened["group"] == top_unf, "空格必须查看未筛选区最上方组")
    # 点击第3张 → 同一界面定位 idx=2
    gp = next(p for p, g in w1.groups.items() if len(g.rgb) >= 4)
    w1._open_group_zoom_at(gp, str(w1.samples[gp][2]))
    expect(opened["group"] == gp and opened["idx"] == 2, "点击定位失败")
    print("4-5) 空格未筛选区 + 点击/空格同一界面 OK")

    # ============ 6. 顶栏布局 ============
    for name in ("btn_count", "btn_import", "btn_export"):
        b = getattr(w1, name)
        expect(b.width() <= b.sizeHint().width() + 30, f"{name} 按钮过宽: {b.width()}")
    expect(w1.btn_switch.x() > 1500, "切换按钮应最右")
    expect(not hasattr(w1, "stats_lbl") and not hasattr(w1, "lbl_root"), "顶栏不应有统计/路径残留")
    for name in ("btn_copy", "btn_import", "btn_export"):
        b = getattr(w2, name)
        expect(b.width() <= b.sizeHint().width() + 30, f"工具2 {name} 按钮过宽")
    expect(not hasattr(w2, "stats_lbl") and not hasattr(w2, "lbl_root"), "工具2顶栏不应有统计/路径残留")
    print("6) 顶栏布局 OK")

    shell.close()
    w1.close()
    w2.close()


if __name__ == "__main__":
    main()
