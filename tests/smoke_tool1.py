"""工具1 冒烟测试（离屏运行）：加载、移动、撤销、一键块操作、导出、导入校验、截图。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from datetime import datetime, timedelta

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

import common_ui as cu
import tool1_filter as t1

HERE = Path(__file__).resolve().parent
DUMMY = HERE.parent / "filtering" / "test_data"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(t1.TOOL_QSS)


# 离屏模式自动关闭模态对话框，避免测试永久阻塞
def _close_modals():
    w = QApplication.activeModalWidget()
    if isinstance(w, QMessageBox):
        w.reject()


_auto = QTimer()
_auto.timeout.connect(_close_modals)
_auto.start(80)


def pump_events(ms=300):
    """持续处理事件一段时间（让异步任务与分批构建推进）。"""
    end = datetime.now() + timedelta(milliseconds=ms)
    while datetime.now() < end:
        app.processEvents()


def main():
    win = t1.MainWindow()
    win.resize(1600, 900)
    win.show()
    app.processEvents()

    # 1. 加载数据集
    win.load_dataset(DUMMY)
    assert win.root == DUMMY.resolve(), "root mismatch"
    assert len(win.groups) == 11, f"组数应为11, 实际 {len(win.groups)}"
    # 等待分批构建完成
    for _ in range(200):
        app.processEvents()
        if not win._build_plan and not win._build_timer.isActive():
            break
    pump_events(1200)  # 让缩略图加载
    assert len(win.cards) == 11, f"卡片数 {len(win.cards)}"
    total_rgb = sum(len(g.rgb) for g in win.groups.values())
    assert total_rgb == 39, f"RGB总数 {total_rgb}"  # 5+5+3+4+2+6+4+0+3+7=39
    assert len(win.blocks) == 3, f"块数 {len(win.blocks)}"

    # 1.5 几何断言：缩略图必须真实排布（防止“数据已加载但控件不可见”类bug漏检）
    strips = [w for w in win.panel_mid.findChildren(cu.ThumbStrip)]
    assert strips, "未找到缩略图条"
    nonempty = [s for s in strips if s._entries]
    for s in nonempty:
        assert s.height() >= 80, f"缩略图条高度异常: {s.height()}"
        geos = [(e["lbl"].geometry().x(), e["lbl"].geometry().y()) for e in s._entries]
        assert len(set(geos)) == len(geos), f"标签位置重叠，未正确排布: {geos}"
    all_labels = [e["lbl"] for s in nonempty for e in s._entries]
    loaded = sum(1 for l in all_labels if l.property("loaded"))
    # 懒加载：视口内（含缓冲带）的缩略图应加载，其余保持占位
    assert loaded >= 10, f"可见缩略图未加载: {loaded}/{len(all_labels)}"
    loaded_lbls = [l for l in all_labels if l.property("loaded")]
    assert all(not l.pixmap().isNull() for l in loaded_lbls), "已加载标签存在空pixmap"

    # 2. 移动：选中顶部、排除新顶部
    top1 = win._top_unfiltered()
    win._move(top1, "sel")
    assert win.selected[0] == top1
    top2 = win._top_unfiltered()
    assert top2 != top1
    win._move(top2, "exc")
    assert win.excluded[0] == top2

    # 3. 一键排除第一个块（剩余组）
    b0 = next(iter(win.unfiltered_blocks))
    n_before = len(win.unfiltered_blocks[b0])
    win._exclude_block(b0)
    assert len(win.unfiltered_blocks.get(b0, [])) == 0
    assert len(win.excluded) == 1 + n_before
    assert all(p in win.groups for p in win.excluded)

    # 4. 撤销一次（恢复到未筛选区最上方，且其块应置于最前）
    win._undo()
    assert len(win.history) == 1 + n_before, f"history={len(win.history)} n_before={n_before}"
    first_block = next(iter(win.unfiltered_blocks))
    assert first_block == b0, f"恢复后块应置顶: {first_block}"

    # 5. 选中区恢复按钮路径
    p = win.selected[0]
    win._restore_group(p)
    assert p not in win.selected
    assert win._top_unfiltered() == p

    # 5.5 重组筛选状态：恢复全部已排除组，选中前6个组（覆盖 (5,5)/(5,4)/(3,5)/(4,4)/(2,0)/(6,6)
    #     多种配对情形，供工具2测试），再排除1个组
    while win.excluded:
        win._restore_group(win.excluded[-1])
    for _ in range(6):
        top = win._top_unfiltered()
        if top is None:
            break
        win._move(top, "sel")
    assert len(win.selected) == 6
    win._move(win._top_unfiltered(), "exc")
    assert len(win.excluded) == 1

    # 6. 导出
    state = win.collect_state()
    sj = OUT / "state1.json"
    assert win.export_to(sj)
    data = json.loads(sj.read_text(encoding="utf-8"))
    st = data["统计信息"]
    assert st["总组数"] == 11 and st["总块数"] == 3
    assert st["总RGB图数"] == 39
    assert st["选中组数"] == len(data["选中区"]) == len(win.selected)
    assert st["排除组数"] == len(data["排除区"]) == len(win.excluded)
    n_unf = len(data["未筛选区"])
    assert n_unf + len(win.selected) + len(win.excluded) == 11

    # 7. 导入：路径不匹配应拒绝
    bad = dict(data)
    bad["统计信息"] = dict(st)
    bad["统计信息"]["数据集根目录绝对路径"] = r"D:\not\exist"
    badf = OUT / "bad.json"
    badf.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    assert win.import_state(badf) is False, "路径不匹配应拒绝导入"

    # 8. 导入：路径匹配应成功并还原
    assert win.import_state(sj) is True
    assert win.selected == data["选中区"]
    assert win.excluded == data["排除区"]

    # 9. count 保存 → 重采样
    old = win.count
    win.edit_count.setText("3")
    win._save_count()
    assert win.count == 3
    for p, s in win.samples.items():
        g = win.groups[p]
        assert len(s) == min(3, len(g.rgb))
    win.edit_count.setText(str(old))
    win._save_count()

    # 10. 截图
    pump_events(1500)
    win.grab().save(str(OUT / "tool1.png"))
    win.manager.wait(4000)
    win.grab().save(str(OUT / "tool1_loaded.png"))
    print("tool1 smoke OK")
    print(json.dumps(state["统计信息"], ensure_ascii=False, indent=1))
    win.close()


if __name__ == "__main__":
    main()
