"""工具2 冒烟测试（离屏运行）：导入工具1状态、配对、排除/恢复、整组排除、导出、拷贝、截图。"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

import tool2_refine as t2

HERE = Path(__file__).resolve().parent
DUMMY = HERE.parent / "filtering" / "test_data"
OUT = HERE / "out"
STATE1 = OUT / "state1.json"
COPY_DEST = HERE / "out_data"

app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(t2.TOOL_QSS)


# 离屏模式自动关闭模态对话框，避免测试永久阻塞
def _close_modals():
    w = QApplication.activeModalWidget()
    if isinstance(w, QMessageBox):
        w.reject()


_auto = QTimer()
_auto.timeout.connect(_close_modals)
_auto.start(80)


def pump_events(ms=300):
    end = datetime.now() + timedelta(milliseconds=ms)
    while datetime.now() < end:
        app.processEvents()


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    if not STATE1.exists():
        raise SystemExit("请先运行 tests/smoke_tool1.py 生成 state1.json")
    if COPY_DEST.exists():
        shutil.rmtree(COPY_DEST)

    win = t2.MainWindow()
    win.resize(1500, 900)
    win.show()
    app.processEvents()

    # 1. 导入工具1状态
    expect(win.import_json(STATE1), "导入失败")
    sel = json.loads(STATE1.read_text(encoding="utf-8"))["选中区"]
    expect(len(win.groups) == len(sel), f"组数 {len(win.groups)} != 选中区 {len(sel)}")
    expect(win.root == DUMMY.resolve(), f"root {win.root}")
    pump_events(800)

    # 2. 模拟数据集配对情况校验（以某个 (5,5) 组为例）
    target = None
    for gp, gd in win.groups.items():
        if len(gd.rgb) == 5 and len(gd.depth) == 5:
            target = gd
            break
    expect(target is not None, "找不到 (5,5) 组")
    expect(len(target.pairs) == 5 and all(p.rgb and p.depth for p in target.pairs), "(5,5) 组应全部成对")
    target45 = next(gd for gd in win.groups.values() if len(gd.rgb) == 5 and len(gd.depth) == 4)
    pairs45 = target45.pairs
    expect(sum(1 for p in pairs45 if p.rgb and p.depth) == 4, "(5,4) 组应有4对")
    expect(sum(1 for p in pairs45 if p.rgb and not p.depth) == 1, "(5,4) 组应有1仅RGB")

    # 3. 选中一个组显示工作区
    win._select_group_item(str(target.path))
    expect(win.current_group == str(target.path), "工作区未切换")
    cards = [w for i in range(win.ws_lay.count())
             if (w := win.ws_lay.itemAt(i).widget()) is not None and isinstance(w, t2.PairCard)]
    expect(len(cards) == 5, f"卡片数 {len(cards)}")

    # 4. 排除一个图片对 → 统计应同步
    card0 = cards[0]
    rgb0, depth0 = card0.pair.rgb, card0.pair.depth
    win._toggle_image_excluded(str(rgb0), False)
    win._toggle_image_excluded(str(depth0), True)
    expect(rgb0 in target.exc_rgb and depth0 in target.exc_depth, "排除未生效")
    expect(target.remaining(), "组仍应保留")

    # 5. 恢复
    win._toggle_image_excluded(str(rgb0), False)
    win._toggle_image_excluded(str(depth0), True)
    expect(rgb0 not in target.exc_rgb and depth0 not in target.exc_depth, "恢复未生效")

    # 6. 排除整组 → 从树中消失，进入已排除组节点
    gp_x = str(target45.path)
    win._select_group_item(gp_x)
    expect(win.current_group == gp_x, "切换失败")
    win._set_group_excluded(gp_x, True)
    expect(not target45.remaining(), "整组排除失败")
    expect(win._tree_groups[gp_x].parent() is win._excl_node, "组未进入已排除节点")
    # 恢复整组
    win._set_group_excluded(gp_x, False)
    expect(target45.remaining(), "恢复整组失败")

    # 7. 排除到完全空 → 自动前进 + 移入已排除节点
    g_dead = next(gd for gd in win.groups.values() if len(gd.rgb) == 2 and len(gd.depth) == 0)
    gp_d = str(g_dead.path)
    win._select_group_item(gp_d)
    for p in list(g_dead.rgb):
        win._toggle_image_excluded(str(p), False)
    expect(not g_dead.remaining(), "组应被完全排除")
    expect(win._tree_groups[gp_d].parent() is win._excl_node, "空组未进入已排除节点")
    expect(win.current_group != gp_d, "应自动切换到下一个组")

    # 8. 导出
    state = win.collect_state()
    f2 = OUT / "state2.json"
    expect(win.export_to(f2), "导出失败")
    data = json.loads(f2.read_text(encoding="utf-8"))
    st = data["统计信息"]
    expect(st["最终保留的总RGB图数"] == len(data["图片对"]) + len(data["仅RGB图"]), "RGB统计不一致")
    expect(st["最终保留的总深度图数"] == len(data["图片对"]) + len(data["仅深度图"]), "深度统计不一致")
    expect(st["最终保留的总组数"] == sum(1 for gd in win.groups.values() if gd.remaining()), "组统计不一致")
    # 已完全排除的组 g_dead 不应出现在导出中
    all_exported = set(data["图片对"]) | set(data["仅RGB图"]) | set(data["仅深度图"])
    expect(not any(g_dead.rel in p for p in all_exported), "已排除组不应出现在导出")

    # 9. 拷贝（同步核心）
    files = t2.collect_copy_files(win.groups)
    expect(files, "没有可拷贝文件")
    done, errs = t2.perform_copy(files, COPY_DEST)
    expect(not errs and done == len(files), f"拷贝失败 {errs[:2]}")
    for src, rel in files:
        expect((COPY_DEST / Path(rel)).is_file(), f"缺失拷贝文件 {rel}")
    # 结构校验：包含 块/二级/场景/color 前缀
    expect(all(len(Path(rel).parts) == 5 for _, rel in files), "拷贝路径层级不符")

    # 10. 快捷键入口（不真正打开模态框，仅校验状态）
    pump_events(1200)
    win.grab().save(str(OUT / "tool2.png"))
    win.manager.wait(4000)
    win.grab().save(str(OUT / "tool2_loaded.png"))
    print("tool2 smoke OK")
    print(json.dumps(st, ensure_ascii=False, indent=1))
    win.close()


if __name__ == "__main__":
    main()
