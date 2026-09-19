"""工具1：数据集初步筛选（RGB 图组级筛选）。

界面：左侧选中区 / 中间未筛选区（按 块-组 组织，占据主要宽度）/ 右侧排除区。
功能：选择数据集、每组随机抽样显示数量 count、导出 / 导入筛选状态（JSON）、
组级 选中 / 排除 / 恢复、块级一键选中 / 一键排除。

快捷键（默认作用于区域最上方的组）：
    A  选中最上方未筛选组
    D  排除最上方未筛选组
    S  撤销上一步（从选中区/排除区恢复到未筛选区最上方）
    空格  放大查看未筛选区最上方的组
          放大查看中：A 上一张 · D 下一张 · Q/Esc 退出
"""
from __future__ import annotations

import json
import os
import random
import sys
import traceback
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStatusBar,
                               QVBoxLayout, QWidget)

import common_ui as cu

PROJECT_DIR = Path(__file__).resolve().parent.parent  # 项目根目录（脚本位于 src/ 内）
FILTERING_DIR = PROJECT_DIR / "filtering"             # 工作目录：状态JSON 默认读写处
ORIGINAL_DATA_DIR = FILTERING_DIR / "original_data"   # 原始数据集存放目录
COPY_DATA_DIR = FILTERING_DIR / "data"                # 拷贝输出目录


def _ensure_work_dirs():
    for d in (FILTERING_DIR, ORIGINAL_DATA_DIR, COPY_DATA_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


DEFAULT_COUNT = 15
MID_COLS = 5         # 未筛选区目标列数（缩略图随视口自适应）
SIDE_COLS = 3        # 选中/排除区目标列数
THUMB = 136          # 未筛选区缩略图默认尺寸（视口未就绪时回退）
THUMB_SIDE = 104     # 选中/排除区默认尺寸
_MID_RANGE = (96, 240)
_SIDE_RANGE = (64, 180)


def _panel_thumb(view_w: int, cols: int, rng) -> int:
    """按视口宽度计算一行 cols 张时的缩略图边长（8px 量化，抑制滚动条抖动）。"""
    if view_w <= 0:
        return 0
    raw = (view_w - 48 - (cols - 1) * 4) / cols   # 面板/卡片/滚动区边距与列间距
    v = max(rng[0], min(rng[1], int(raw)))
    return v // 8 * 8

TOOL_QSS = """
QMainWindow, QDialog, QMenu { background:#0D1117; color:#E6EDF3; }
QMessageBox QLabel { color:#E6EDF3; background:transparent; }
QToolTip { background:#1A212B; color:#E6EDF3; border:1px solid #30363D; }

QFrame#TopBar { background:#161B22; border-bottom:1px solid #21262D; }
QLabel#navCountLbl { color:#F0F6FC; font-weight:600; }
QFrame#AreaPanel { background:#161B22; border:1px solid #30363D; border-radius:10px; }
QFrame#AreaPanel[accent="sel"] { border-top:3px solid #3FB950; }
QFrame#AreaPanel[accent="mid"] { border-top:3px solid #58A6FF; }
QFrame#AreaPanel[accent="exc"] { border-top:3px solid #F85149; }
QLabel#AreaTitle { font-size:14px; font-weight:600; color:#F0F6FC; }
QLabel#AreaCount { color:#8B949E; }
QScrollArea#AreaScroll { border:none; background:transparent; }
QWidget#AreaContent { background:transparent; }

QFrame#BlockSection { background:#10151C; border:1px solid #262D37; border-radius:8px; }
QLabel#BlockName { font-weight:600; color:#F0F6FC; }
QLabel#BlockStats { color:#8B949E; }

QFrame#GroupCard { background:#1A212B; border:1px solid #30363D; border-radius:8px; }
QFrame#GroupCard:hover { border-color:#484F58; }
QFrame#GroupCard[side="sel"] { border-left:4px solid #3FB950; }
QFrame#GroupCard[side="exc"] { border-left:4px solid #F85149; }
QFrame#GroupCard[flash="true"], QFrame#BlockSection[flash="true"] { background:#4A3808; border-color:#E3B341; }

QPushButton { padding:4px 12px; border:1px solid #30363D; border-radius:6px; background:#21262D; color:#E6EDF3; }
QPushButton:hover { background:#292F3A; border-color:#484F58; }
QPushButton:disabled { color:#6E7681; background:#161B22; }
QPushButton#btnPrimary { background:#1F6FEB; color:#FFFFFF; border-color:#1F6FEB; font-weight:600; }
QPushButton#btnPrimary:hover { background:#388BFD; }
QPushButton#chipSelect { color:#3FB950; border-color:#23863B; background:#0F2A1B; }
QPushButton#chipSelect:hover { background:#164A2E; }
QPushButton#chipExclude { color:#F85149; border-color:#B62324; background:#2D1416; }
QPushButton#chipExclude:hover { background:#4A1D20; }
QPushButton#chipRestore { color:#58A6FF; border-color:#1F6EBB; background:#0E2233; }
QPushButton#chipRestore:hover { background:#14375A; }
QPushButton#btnToggle { background:transparent; border:none; color:#8B949E; font-weight:700; }

QLineEdit { padding:3px 8px; border:1px solid #30363D; border-radius:6px; background:#0D1117; color:#F0F6FC; selection-background-color:#1F6FEB; }
QStatusBar { background:#161B22; color:#8B949E; }

QScrollBar:vertical { background:transparent; width:10px; margin:2px; }
QScrollBar::handle:vertical { background:#30363D; border-radius:5px; min-height:24px; }
QScrollBar::handle:vertical:hover { background:#3D444D; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar:horizontal { background:transparent; height:10px; margin:2px; }
QScrollBar::handle:horizontal { background:#30363D; border-radius:5px; min-width:24px; }
QScrollBar::handle:horizontal:hover { background:#3D444D; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
QScrollBar::add-page, QScrollBar::sub-page { background:transparent; }
"""

ACCENT_COLOR = {"sel": "#3FB950", "mid": "#58A6FF", "exc": "#F85149"}


class GroupCard(QFrame):
    """一个组的卡片：标题 + 图片数 + 操作按钮 + 缩略图条。"""

    def __init__(self, group: cu.Group, sample, manager, side, actions, thumb=THUMB, parent=None):
        super().__init__(parent)
        self.side = side
        self.path = str(group.path)
        self.setObjectName("GroupCard")
        self.setProperty("side", side)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 5)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{ACCENT_COLOR[side]};font-size:13px;")
        head.addWidget(dot)
        title = cu.ElidedLabel(group.rel if side == "mid" else str(group.path))
        title.setToolTip(str(group.path))
        title.setStyleSheet("font-weight:600;color:#F0F6FC;")
        head.addWidget(title, 1)
        shown = f"{len(group.rgb)}张" + (f" · 显示{len(sample)}" if len(sample) != len(group.rgb) else "")
        cnt = QLabel(shown)
        cnt.setStyleSheet("color:#8B949E;")
        head.addWidget(cnt)
        if side == "mid":
            b_sel = QPushButton("选中")
            b_sel.setObjectName("chipSelect")
            b_sel.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 避免按钮抢占空格等快捷键
            b_sel.clicked.connect(lambda _=False, p=self.path: actions["select"](p))
            b_exc = QPushButton("排除")
            b_exc.setObjectName("chipExclude")
            b_exc.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b_exc.clicked.connect(lambda _=False, p=self.path: actions["exclude"](p))
            head.addWidget(b_sel)
            head.addWidget(b_exc)
        else:
            b_res = QPushButton("恢复到未筛选")
            b_res.setObjectName("chipRestore")
            b_res.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b_res.clicked.connect(lambda _=False, p=self.path: actions["restore"](p))
            head.addWidget(b_res)
        lay.addLayout(head)

        strip = cu.ThumbStrip(manager, [(p, False) for p in sample], thumb,
                              fit_height=True, spacing=4)  # 图片加载后贴紧排布
        # 点击/右键查看 → 与空格一致的组内浏览界面（定位到被点图片）
        strip.viewRequested.connect(lambda p, d: actions["zoom_at"](p))
        lay.addWidget(strip)
        if not sample:
            empty = QLabel("（该组无RGB图）")
            empty.setStyleSheet("color:#9AA4AF;")
            lay.addWidget(empty)


class BlockSection(QFrame):
    """未筛选区中一个块（一级目录）的容器：块头 + 组卡片列表。"""

    selectAll = Signal(str)
    excludeAll = Signal(str)
    collapsedChanged = Signal(str, bool)   # (块名, 是否折叠)，供重建时保持折叠状态

    def __init__(self, block_name, collapsed=False, parent=None):
        super().__init__(parent)
        self.block = block_name
        self.setObjectName("BlockSection")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 4)
        lay.setSpacing(3)

        head = QFrame()
        head.setObjectName("BlockHead")
        hlay = QHBoxLayout(head)
        hlay.setContentsMargins(4, 2, 4, 2)
        hlay.setSpacing(8)
        self.btn_toggle = QPushButton("▾")
        self.btn_toggle.setObjectName("btnToggle")
        self.btn_toggle.setFixedWidth(22)
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        name = QLabel(block_name)
        name.setObjectName("BlockName")
        self.stats = QLabel("")
        self.stats.setObjectName("BlockStats")
        self.btn_all_sel = QPushButton("一键选中")
        self.btn_all_sel.setObjectName("chipSelect")
        self.btn_all_sel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_all_exc = QPushButton("一键排除")
        self.btn_all_exc.setObjectName("chipExclude")
        self.btn_all_exc.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hlay.addWidget(self.btn_toggle)
        hlay.addWidget(name)
        hlay.addWidget(self.stats)
        hlay.addStretch(1)
        hlay.addWidget(self.btn_all_sel)
        hlay.addWidget(self.btn_all_exc)

        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(6, 2, 6, 0)
        self.body_lay.setSpacing(6)
        self.body_lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        lay.addWidget(head)
        lay.addWidget(self.body)

        self.btn_toggle.clicked.connect(self.toggle_collapse)
        self.btn_all_sel.clicked.connect(lambda: self.selectAll.emit(self.block))
        self.btn_all_exc.clicked.connect(lambda: self.excludeAll.emit(self.block))
        if collapsed:
            self.body.setVisible(False)
            self.btn_toggle.setText("▸")

    def toggle_collapse(self):
        collapsed = self.body.isVisible()
        self.body.setVisible(not collapsed)
        self.btn_toggle.setText("▸" if collapsed else "▾")
        self.collapsedChanged.emit(self.block, collapsed)

    def add_card(self, card, top=False):
        self.body_lay.insertWidget(0 if top else self.body_lay.count(), card)

    def set_stats(self, groups: int, images: int):
        self.stats.setText(f"　{groups} 组 · {images} 张")
        self.btn_all_sel.setEnabled(groups > 0)
        self.btn_all_exc.setEnabled(groups > 0)


class EmptyBlocksBar(QWidget):
    """空块折叠条：一行开关 + 可展开的块名标签流。"""

    expandedChanged = Signal(bool)

    def __init__(self, names, expanded=False, parent=None):
        super().__init__(parent)
        self._names = list(names)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 2, 6, 2)
        v.setSpacing(4)
        self.btn = QPushButton()
        self.btn.setObjectName("btnToggle")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn.clicked.connect(self._toggle)
        v.addWidget(self.btn)
        chips = QWidget()
        self.chips_lay = cu.FlowLayout(chips, margin=0, hspacing=6, vspacing=4)
        for n in self._names:
            chip = QLabel(n)
            chip.setStyleSheet(
                "background:#161B22;border:1px solid #30363D;border-radius:10px;"
                "padding:2px 8px;color:#8B949E;")
            self.chips_lay.addWidget(chip)
        chips.setVisible(expanded)
        self._chips = chips
        v.addWidget(chips)
        self._apply_text(expanded)

    def _toggle(self):
        expanded = not self._chips.isVisible()
        self._chips.setVisible(expanded)
        self._apply_text(expanded)
        self.expandedChanged.emit(expanded)

    def _apply_text(self, expanded):
        self.btn.setText(f"{'▾' if expanded else '▸'} 空块 {len(self._names)} 个"
                         f"（{'点击收起' if expanded else '点击展开'}）")


class AreaPanel(QFrame):
    """左 / 中 / 右三个区域面板：标题 + 计数 + 滚动内容。"""

    def __init__(self, title, accent, manager, parent=None):
        super().__init__(parent)
        self.setObjectName("AreaPanel")
        self.setProperty("accent", accent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        head = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{ACCENT_COLOR[accent]};font-size:15px;")
        t = QLabel(title)
        t.setObjectName("AreaTitle")
        self.count_lbl = QLabel("0 组")
        self.count_lbl.setObjectName("AreaCount")
        head.addWidget(dot)
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(self.count_lbl)
        lay.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("AreaScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.content.setObjectName("AreaContent")
        self.vlay = QVBoxLayout(self.content)
        self.vlay.setContentsMargins(2, 2, 2, 2)
        self.vlay.setSpacing(6)
        self.vlay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.content)
        lay.addWidget(self.scroll, 1)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _v: manager.schedule_reprioritize())

    def clear(self):
        while self.vlay.count():
            item = self.vlay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()            # 先隐藏再移除，避免重挂载瞬间闪现小窗口
                w.setParent(None)
                w.deleteLater()

    def scroll_top(self):
        self.scroll.verticalScrollBar().setValue(0)

    def set_count(self, n: int):
        self.count_lbl.setText(f"{n} 组")


class GroupZoomDialog(QDialog):
    """组内放大浏览：空格进入，或点击缩略图定位进入；A/D 翻页，Q 退出。"""

    def __init__(self, parent, group: cu.Group, paths, start_index: int = 0):
        super().__init__(parent)
        self.setWindowTitle(f"放大查看 - {group.rel}")
        self.resize(1280, 820)
        self.paths = [str(p) for p in paths]
        self.idx = start_index % len(self.paths) if self.paths else 0
        lay = QVBoxLayout(self)
        self.caption = QLabel("")
        self.caption.setStyleSheet("font-weight:600;")
        self.caption.setWordWrap(True)
        lay.addWidget(self.caption)
        self.pane = cu.FitImagePane()
        lay.addWidget(self.pane, 1)
        btns = QHBoxLayout()
        b_prev = QPushButton("上一张 (A)")
        b_next = QPushButton("下一张 (D)")
        self.b_copy = QPushButton("复制图片")
        b_quit = QPushButton("退出 (Q)")
        btns.addWidget(b_prev)
        btns.addWidget(b_next)
        btns.addStretch(1)
        btns.addWidget(self.b_copy)
        btns.addWidget(b_quit)
        lay.addLayout(btns)
        hint = QLabel("A 上一张 · D 下一张 · Q / Esc 退出 · 滚轮缩放 · 双击适应窗口")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color:#8B949E;")
        lay.addWidget(hint)
        b_prev.clicked.connect(lambda: self._step(-1))
        b_next.clicked.connect(lambda: self._step(1))
        self.b_copy.clicked.connect(lambda: cu.copy_image_to_clipboard(self.paths[self.idx]))
        b_quit.clicked.connect(self.accept)
        for key, fn in (("A", lambda: self._step(-1)), ("D", lambda: self._step(1)),
                        ("Left", lambda: self._step(-1)), ("Right", lambda: self._step(1)),
                        ("Q", self.accept)):
            QShortcut(QKeySequence(key), self).activated.connect(fn)
        self._show_idx()

    def _step(self, delta):
        if not self.paths:
            return
        self.idx = (self.idx + delta) % len(self.paths)
        self._show_idx()

    def _show_idx(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            img = cu.load_qimage(self.paths[self.idx], 2200)
        finally:
            QApplication.restoreOverrideCursor()
        self.pane.set_image(img)
        name = Path(self.paths[self.idx]).name
        self.caption.setText(f"{self.idx + 1} / {len(self.paths)}　{name}")


class MainWindow(QWidget):
    """工具1页面（可独立运行，也可嵌入 ToolShell 与工具2同窗切换）。"""

    switchRequested = Signal(str)   # 请求宿主切换到另一工具

    def __init__(self):
        super().__init__()
        self.resize(1680, 940)
        _ensure_work_dirs()
        self.manager = cu.ThumbManager(self)

        # ---- 状态
        self.root = None
        self.groups = {}                       # str(组绝对路径) -> cu.Group
        self.blocks = OrderedDict()            # 块名 -> 组路径列表（扫描顺序）
        self.unfiltered_blocks = OrderedDict() # 块名 -> 组路径列表（当前显示顺序）
        self.selected = []                     # 选中区（最新在前）
        self.excluded = []                     # 排除区（最新在前）
        self.history = []                      # [(动作"sel"/"exc", 组路径)]
        self.samples = {}                      # 组路径 -> 抽样显示的图片列表
        self.count = DEFAULT_COUNT
        self.cards = {}                        # (区域, 组路径) -> GroupCard
        self.block_widgets = {}                # 块名 -> BlockSection
        self._collapsed = set()
        self._empty_expanded = False   # 空块折叠条的展开状态（跨刷新保留）
        self._empty_row = None
        self._thumbs_in_use = {}       # 区域 -> 当前缩略图边长（视口变化时判断是否重排）
        self._build_plan = []
        self._build_timer = QTimer(self)
        self._build_timer.setInterval(10)
        self._build_timer.timeout.connect(self._build_tick)

        self._build_ui()
        self._set_dataset_enabled(False)
        self.setFocus(Qt.FocusReason.OtherFocusReason)  # 初始焦点在主窗口，快捷键立即可用

    # ------------------------------------------------ UI 构建
    def _build_ui(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        hlay = QHBoxLayout(bar)
        hlay.setContentsMargins(10, 6, 10, 6)
        hlay.setSpacing(8)
        self.btn_dataset = QPushButton("选择数据集")
        self.btn_dataset.setObjectName("btnPrimary")
        self.btn_dataset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lbl_count = QLabel("每组显示数量 count")
        lbl_count.setObjectName("navCountLbl")
        lbl_count.setToolTip("组内图片多于该数量时随机抽样显示；范围 1~500")
        self.edit_count = QLineEdit(str(DEFAULT_COUNT))
        self.edit_count.setMaximumWidth(72)
        self.edit_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit_count.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # 点击才聚焦：避免启动时占用快捷键输入
        self.edit_count.setToolTip("组内图片多于该数量时随机抽样显示；范围 1~500，点击「保存」生效")
        self.btn_count = QPushButton("保存")
        self.btn_count.setObjectName("chipRestore")
        self.btn_count.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_export = QPushButton("导出json")
        self.btn_import = QPushButton("导入json")
        self.btn_switch = QPushButton("→ 数据精筛")
        self.btn_switch.setToolTip("同窗切换到工具2（RGB/深度精筛）：首次自动带上数据集与选中区，两侧工作状态各自保留")
        self.btn_json_help = QPushButton("JSON说明")
        self.btn_json_help.setToolTip("查看两个工具导出 JSON 的结构说明")
        for b in (self.btn_dataset, self.btn_count, self.btn_export, self.btn_import,
                  self.btn_switch, self.btn_json_help):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)  # 宽度贴合文字，不随布局拉伸
        # 布局：[选择数据集][数量 输入 保存 导入json 导出json] ……弹簧…… [→工具2]（数据集路径与统计显示在左下角状态栏）
        hlay.addWidget(self.btn_dataset)
        hlay.addWidget(lbl_count)
        hlay.addWidget(self.edit_count)
        hlay.addWidget(self.btn_count)
        hlay.addWidget(self.btn_import)
        hlay.addWidget(self.btn_export)
        hlay.addStretch(1)
        hlay.addWidget(self.btn_json_help)
        hlay.addWidget(self.btn_switch)   # 切换按钮最右

        self.panel_sel = AreaPanel("选中区", "sel", self.manager)
        self.panel_mid = AreaPanel("未筛选区", "mid", self.manager)
        self.panel_exc = AreaPanel("排除区", "exc", self.manager)
        # 侧区最小宽度：保证最小窗口下 3 列缩略图放得下
        self.panel_sel.setMinimumWidth(320)
        self.panel_exc.setMinimumWidth(320)
        split = QSplitter()
        split.addWidget(self.panel_sel)
        split.addWidget(self.panel_mid)
        split.addWidget(self.panel_exc)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 8)
        split.setStretchFactor(2, 3)
        split.setSizes([360, 940, 360])

        self.hint_lbl = QLabel("请先点击上方「选择数据集」，选择数据集根目录并加载")
        self.hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_lbl.setStyleSheet("color:#9AA4AF;font-size:15px;padding:40px;")
        self.panel_mid.vlay.addWidget(self.hint_lbl)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        v.addWidget(bar)
        v.addWidget(split, 1)

        # 页面结构：主内容 + 内嵌状态栏（作为 QWidget 页面嵌入 ToolShell）
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(central, 1)
        self._statusbar = QStatusBar()
        root.addWidget(self._statusbar)

        self.status_lbl = QLabel("")
        self.statusBar().addWidget(self.status_lbl)
        self.notify_lbl = QLabel("")
        self.notify_lbl.setStyleSheet("color:#D29922;")
        self.statusBar().addWidget(self.notify_lbl)
        self._notify_timer = QTimer(self)
        self._notify_timer.setSingleShot(True)
        self._notify_timer.timeout.connect(lambda: self.notify_lbl.setText(""))
        self.thumb_progress_lbl = QLabel("")
        self.thumb_progress_lbl.setStyleSheet("color:#D29922;")
        self.statusBar().addPermanentWidget(self.thumb_progress_lbl)
        self.manager.progress.connect(self._on_thumb_progress)
        help_lbl = QLabel("A选中 · D排除 · 右键复制 · 左键/空格：放大查看图片（A上一张 · D下一张 · Q退出）")
        help_lbl.setStyleSheet("color:#8B949E;")
        self.statusBar().addPermanentWidget(help_lbl)

        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(200)
        self._relayout_timer.timeout.connect(self._relayout_if_needed)
        for panel in (self.panel_sel, self.panel_mid, self.panel_exc):
            panel.scroll.installEventFilter(self)

        self.btn_dataset.clicked.connect(self._choose_dataset)
        self.btn_count.clicked.connect(self._save_count)
        self.btn_export.clicked.connect(self._export_dialog)
        self.btn_import.clicked.connect(self._import_dialog)
        self.btn_switch.clicked.connect(self._switch_to_tool2)
        self.btn_json_help.clicked.connect(
            lambda: cu.JsonHelpDialog("tool1", self).exec())

        # 快捷键限定在本页面内生效（同窗双页面下避免串扰）
        for key, fn in (("A", self._hk_select), ("D", self._hk_exclude),
                        ("S", self._hk_undo), ("Space", self._hk_zoom)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Resize and self.root is not None:
            self._relayout_timer.start()
        return super().eventFilter(obj, ev)

    def _card_strip_width(self, area):
        """取该区域任一现存卡片的条宽（卡片宽度与缩略图尺寸无关，读数稳定）。"""
        for (a, _gp), card in self.cards.items():
            if a == area:
                return card.width() - 12
        return None

    def _relayout_if_needed(self):
        """视口尺寸变化后，若目标缩略图尺寸改变则全量重排（保持目标列数）。

        仅对已有卡片的区域按“实际卡片宽度”（与缩略图尺寸无关，读数稳定）重算；
        空区域无卡片可量，维持当前尺寸即可。
        """
        if self.root is None or self._build_plan:
            return
        want = dict(self._thumbs_in_use)
        for area, cols, rng in (("mid", MID_COLS, _MID_RANGE),
                                ("sel", SIDE_COLS, _SIDE_RANGE),
                                ("exc", SIDE_COLS, _SIDE_RANGE)):
            strip_w = self._card_strip_width(area)
            if strip_w is not None:
                v = max(rng[0], min(rng[1], int((strip_w - (cols - 1) * 4) / cols))) // 8 * 8
                want[area] = v
        # 量化后仍有差异才重排（阈值消除滚动条出现/消失引起的宽度抖动震荡）；
        # 先写回目标尺寸再重建，保证重建的卡片采用新尺寸、循环可收敛
        changed = {a: v for a, v in want.items()
                   if abs(v - (self._thumbs_in_use.get(a) or 0)) >= 8}
        if changed:
            self._thumbs_in_use.update(changed)
            self._rebuild_all()

    def _set_dataset_enabled(self, ok: bool):
        self.btn_export.setEnabled(ok)
        self.btn_import.setEnabled(ok)

    def statusBar(self) -> QStatusBar:
        """页面内嵌状态栏（页面嵌入 ToolShell 时随页面显示）。"""
        return self._statusbar

    def _notify(self, msg: str, msec: int = 4000):
        """底部短通知（独立标签，与统计信息互不遮挡）。"""
        self.notify_lbl.setText(msg)
        self._notify_timer.start(msec)

    def _begin_load(self, msg: str):
        """开始加载/导入：清空通知，状态栏切换为过程提示。"""
        self._notify_timer.stop()
        self.notify_lbl.setText("")
        self.status_lbl.setText(msg)
        QApplication.processEvents()

    def _on_thumb_progress(self, done: int, total: int):
        """状态栏实时显示缩略图加载进度与失败计数。"""
        if total and done < total:
            txt = f"缩略图加载中 {done}/{total}"
            if self.manager.failed_count:
                txt += f"（{self.manager.failed_count} 张失败）"
        elif self.manager.failed_count:
            txt = f"⚠ {self.manager.failed_count} 张图片加载失败（格式不受支持或文件损坏）"
        else:
            txt = ""
        self.thumb_progress_lbl.setText(txt)

    # ------------------------------------------------ 数据加载
    def _choose_dataset(self):
        d = QFileDialog.getExistingDirectory(self, "选择数据集根目录", str(FILTERING_DIR))
        if not d:
            return
        self.load_dataset(Path(d))

    def load_dataset(self, root: Path, confirm: bool = True):
        if confirm and (self.selected or self.excluded):
            r = QMessageBox.question(self, "确认重新加载",
                                     "当前已有筛选状态，重新加载数据集将丢失这些状态，是否继续？")
            if r != QMessageBox.StandardButton.Yes:
                return
        self._begin_load(f"正在扫描数据集：{root} …")
        groups = cu.scan_dataset_async(self, root)   # 后台扫描，带进度与取消
        if groups is None:
            self._update_counts()
            return
        if not groups:
            QMessageBox.warning(self, "未发现数据",
                                "该目录下未发现符合结构的数据（需存在包含 color/ 或 depth/ 子目录的场景目录）。")
            self._update_counts()
            return
        if all(len(g.rgb) == 0 for g in groups):
            QMessageBox.information(
                self, "未发现RGB图",
                f"已扫描到 {len(groups)} 个组，但所有组的 color/ 目录下均未发现图片文件。\n\n"
                f"期望结构：场景目录/color/xxx.jpg（.jpg/.jpeg/.png/.bmp/.webp/.tif）。\n"
                f"已兼容：目录名大小写不同、图片位于 color/ 的下一级子目录。\n\n"
                f"请核对数据集结构或图片格式；界面仍将加载，但无法显示RGB缩略图。")
        self.root = Path(root).resolve()
        self.groups = {str(g.path): g for g in groups}
        self.blocks = OrderedDict()
        for p, g in self.groups.items():
            self.blocks.setdefault(g.block, []).append(p)
        self.selected.clear()
        self.excluded.clear()
        self.history.clear()
        self.unfiltered_blocks = OrderedDict((b, list(gs)) for b, gs in self.blocks.items())
        for p in self.groups:
            self._resample(p)
        self._set_dataset_enabled(True)
        self._rebuild_all()
        self._update_counts()

    def _resample(self, path):
        g = self.groups[path]
        rgb = g.rgb
        if len(rgb) > self.count:
            chosen = random.sample(rgb, self.count)
            chosen.sort(key=lambda p: cu.natural_key(p.name))
        else:
            chosen = list(rgb)
        self.samples[path] = chosen

    def _save_count(self):
        txt = self.edit_count.text().strip()
        try:
            n = int(txt)
        except ValueError:
            QMessageBox.warning(self, "提示", "每组显示数量需为正整数。")
            return
        n = max(1, min(500, n))
        self.edit_count.setText(str(n))
        self.count = n
        if self.root is not None:
            for p in self.groups:
                self._resample(p)
            self._rebuild_all()
        self._notify(f"已设置每组显示数量 = {n}")

    # ------------------------------------------------ 界面构建（分批增量渲染）
    def _rebuild_all(self):
        self._build_plan = ([("mid", p) for p in self._flatten_unfiltered()]
                            + [("sel", p) for p in self.selected]
                            + [("exc", p) for p in self.excluded])
        for panel in (self.panel_sel, self.panel_mid, self.panel_exc):
            panel.clear()
        self.cards.clear()
        self.block_widgets.clear()
        if not self._build_timer.isActive():
            self._build_timer.start()

    def _build_tick(self):
        if not self._build_plan:
            self._build_timer.stop()
            self.manager.schedule_reprioritize()
            self._update_counts()
            self._relayout_timer.start()   # 建卡完成后按实际卡片宽度校正一次列数
            return
        for _ in range(min(12, len(self._build_plan))):
            area, path = self._build_plan.pop(0)
            if path not in self.groups or (area, path) in self.cards:
                continue
            if area == "mid":
                if path not in self.unfiltered_blocks.get(self.groups[path].block, []):
                    continue
            elif path not in (self.selected if area == "sel" else self.excluded):
                continue
            self._insert_card(area, path, top=False)
        for b in list(self.block_widgets):
            self._update_block_section_stats(b)

    def _flatten_unfiltered(self):
        return [p for gs in self.unfiltered_blocks.values() for p in gs]

    def _ensure_block_section(self, block) -> BlockSection:
        sec = self.block_widgets.get(block)
        if sec is None:
            sec = BlockSection(block, collapsed=block in self._collapsed)
            sec.selectAll.connect(self._select_block)
            sec.excludeAll.connect(self._exclude_block)
            sec.collapsedChanged.connect(
                lambda b, c: (self._collapsed.add(b) if c else self._collapsed.discard(b)))
            self.panel_mid.vlay.insertWidget(self.panel_mid.vlay.count(), sec)
            self.block_widgets[block] = sec
        return sec

    def _panel_thumb_now(self, panel, cols, rng, fallback):
        v = _panel_thumb(panel.scroll.viewport().width(), cols, rng)
        return v or fallback

    def _insert_card(self, area, path, top) -> GroupCard:
        g = self.groups[path]
        sample = self.samples.get(path, [])
        def zoom_at(p, gp=path):
            return self._open_group_zoom_at(gp, p)
        if area == "mid":
            sec = self._ensure_block_section(g.block)
            sec.setVisible(True)   # 恢复到曾折叠的空块时重新显示该块
            thumb = self._thumbs_in_use.get("mid") or self._panel_thumb_now(
                self.panel_mid, MID_COLS, _MID_RANGE, THUMB)
            self._thumbs_in_use["mid"] = thumb
            card = GroupCard(g, sample, self.manager, "mid",
                             {"select": self._select_group, "exclude": self._exclude_group,
                              "zoom_at": zoom_at}, thumb=thumb)
            sec.add_card(card, top=top)
            if top:
                self.panel_mid.vlay.insertWidget(0, sec)
        else:
            panel = self.panel_sel if area == "sel" else self.panel_exc
            key = "sel" if area == "sel" else "exc"
            thumb = self._thumbs_in_use.get(key)
            if not thumb:
                strip_w = self._card_strip_width(area) or (panel.scroll.viewport().width() - 48)
                raw = int((strip_w - (SIDE_COLS - 1) * 4) / SIDE_COLS)
                thumb = max(_SIDE_RANGE[0], min(_SIDE_RANGE[1], raw)) // 8 * 8
            self._thumbs_in_use[key] = thumb
            card = GroupCard(g, sample, self.manager, area,
                             {"restore": self._restore_group, "zoom_at": zoom_at},
                             thumb=thumb)
            panel.vlay.insertWidget(0 if top else panel.vlay.count(), card)
        self.cards[(area, path)] = card
        cu.flash(card)
        return card

    def _remove_card(self, area, path, block=None):
        card = self.cards.pop((area, path), None)
        if card is None:
            return
        card.setParent(None)
        card.deleteLater()
        if area == "mid" and block is not None:
            self._update_block_section_stats(block)

    def _update_block_section_stats(self, block):
        sec = self.block_widgets.get(block)
        if sec is None:
            return
        gs = self.unfiltered_blocks.get(block, [])
        imgs = sum(len(self.groups[p].rgb) for p in gs)
        sec.set_stats(len(gs), imgs)

    # ------------------------------------------------ 筛选操作
    def _select_group(self, path):
        self._move(path, "sel")

    def _exclude_group(self, path):
        self._move(path, "exc")

    def _restore_group(self, path):
        self._move(path, "mid")

    def _move(self, path, target):
        """把组移动到目标区（sel/exc/mid），遵守“移动到目标区最上方”规则。"""
        if self.root is None or path not in self.groups:
            return
        g = self.groups[path]
        block = g.block
        if target in ("sel", "exc"):
            lst = self.unfiltered_blocks.get(block, [])
            if path not in lst:
                return
            lst.remove(path)
            (self.selected if target == "sel" else self.excluded).insert(0, path)
            self.history.append((target, path))
            self._remove_card("mid", path, block)
            self._insert_card(target, path, top=True)
            panel = self.panel_sel if target == "sel" else self.panel_exc
            panel.scroll_top()
            self.panel_mid.scroll_top()
        else:  # 恢复到未筛选区最上方
            src = "sel" if path in self.selected else ("exc" if path in self.excluded else None)
            if src is None:
                return
            (self.selected if src == "sel" else self.excluded).remove(path)
            cur = self.unfiltered_blocks.pop(block, [])
            cur.insert(0, path)
            od = OrderedDict()
            od[block] = cur
            od.update(self.unfiltered_blocks)
            self.unfiltered_blocks = od
            self._remove_card(src, path)
            self._insert_card("mid", path, top=True)
            self.panel_mid.scroll_top()
        self._update_counts()

    def _select_block(self, block):
        for p in reversed(list(self.unfiltered_blocks.get(block, []))):
            self._move(p, "sel")

    def _exclude_block(self, block):
        for p in reversed(list(self.unfiltered_blocks.get(block, []))):
            self._move(p, "exc")

    def _top_unfiltered(self):
        for gs in self.unfiltered_blocks.values():
            if gs:
                return gs[0]
        return None

    # ------------------------------------------------ 快捷键
    @staticmethod
    def _hotkey_ok():
        return not isinstance(QApplication.focusWidget(), QLineEdit)

    def _hk_select(self):
        if not self._hotkey_ok():
            return
        p = self._top_unfiltered()
        if p:
            self._move(p, "sel")
        else:
            self._notify("未筛选区没有组")

    def _hk_exclude(self):
        if not self._hotkey_ok():
            return
        p = self._top_unfiltered()
        if p:
            self._move(p, "exc")
        else:
            self._notify("未筛选区没有组")

    def _hk_undo(self):
        if not self._hotkey_ok():
            return
        self._undo()

    def _undo(self):
        if not self.history:
            self._notify("没有可恢复的操作")
            return
        _, path = self.history.pop()
        self._move(path, "mid")

    def _hk_zoom(self):
        """空格：仅放大查看未筛选区最上方的组。"""
        if not self._hotkey_ok() or self.root is None:
            return
        path = self._top_unfiltered()
        if not path:
            self._notify("未筛选区没有组")
            return
        self._open_group_zoom_at(path)

    def _open_group_zoom_at(self, gp, img_path=None):
        """打开组内放大浏览（与空格同一界面）；img_path 指定起始图片。"""
        g = self.groups.get(gp)
        if not g:
            return
        paths = self.samples.get(gp, [])
        if not paths:
            return
        idx = 0
        if img_path is not None:
            idx = next((i for i, p in enumerate(paths) if str(p) == str(img_path)), 0)
        GroupZoomDialog(self, g, paths, start_index=idx).exec()

    # ------------------------------------------------ 工具切换
    def _switch_to_tool2(self):
        """同窗切换到工具2：交接当前数据集与选中区，由宿主/页面决定加载或保留。"""
        self._pending_handoff = {
            "root": str(self.root) if self.root else None,
            "selected": list(self.selected),
        }
        self.switchRequested.emit("tool2")

    def apply_handoff(self, root, selected=None):
        """切换接续：数据集相同→保留工作状态；不同→立即清除旧数据集并加载新数据集。"""
        if not root:
            return
        if self.root is not None and str(self.root) == str(root):
            return   # 同一数据集：保留筛选状态
        self._begin_load(f"正在接续数据集：{root} …")
        try:
            self.load_dataset(Path(root), confirm=False)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------ 状态与导入导出
    def _update_counts(self):
        unf = self._flatten_unfiltered()
        self.panel_sel.set_count(len(self.selected))
        self.panel_mid.set_count(len(unf))
        self.panel_exc.set_count(len(self.excluded))
        total_img = sum(len(g.rgb) for g in self.groups.values())

        def img_of(paths):
            return sum(len(self.groups[p].rgb) for p in paths)

        self.status_lbl.setText(
            f"数据集：{self.root or '—'}　｜　块 {len(self.blocks)} · 组 {len(self.groups)} · 图 {total_img} · "
            f"未筛选 {len(unf)}（{img_of(unf)}） · "
            f"已选 {len(self.selected)}（{img_of(self.selected)}） · 已排 {len(self.excluded)}（{img_of(self.excluded)}）")
        for b in list(self.block_widgets):
            self._update_block_section_stats(b)
        self._refresh_empty_blocks()

    def _refresh_empty_blocks(self):
        """多个无组的块折叠为一行开关（可展开块名标签流），避免占用空间。"""
        if getattr(self, "_empty_row", None) is not None:
            self._empty_row.setParent(None)
            self._empty_row.deleteLater()
            self._empty_row = None
        empty = [b for b, gs in self.unfiltered_blocks.items() if not gs]
        for b, sec in self.block_widgets.items():
            sec.setVisible(bool(self.unfiltered_blocks.get(b)))
        if not empty:
            return
        bar = EmptyBlocksBar(empty, expanded=self._empty_expanded)
        bar.expandedChanged.connect(self._on_empty_expanded)
        self.panel_mid.vlay.addWidget(bar)
        self._empty_row = bar

    def _on_empty_expanded(self, expanded: bool):
        self._empty_expanded = expanded

    def collect_state(self) -> dict:
        unf = self._flatten_unfiltered()

        def img_of(paths):
            return sum(len(self.groups[p].rgb) for p in paths)

        return {
            "未筛选区": unf,
            "选中区": list(self.selected),
            "排除区": list(self.excluded),
            "统计信息": {
                "数据集根目录绝对路径": str(self.root) if self.root else "",
                "总块数": len(self.blocks),
                "总组数": len(self.groups),
                "选中组数": len(self.selected),
                "排除组数": len(self.excluded),
                "总RGB图数": img_of(list(self.groups)),
                "选中总RGB图数": img_of(self.selected),
                "排除总RGB图数": img_of(self.excluded),
            },
        }

    def export_to(self, file_path) -> bool:
        data = self.collect_state()
        try:
            Path(file_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"写入文件失败：{e}")
            return False
        return True

    def _export_dialog(self):
        if self.root is None:
            QMessageBox.warning(self, "提示", "请先选择数据集。")
            return
        name = self.root.name if self.root else "数据集"
        default = FILTERING_DIR / f"粗筛_{name}.json"
        f, _ = QFileDialog.getSaveFileName(self, "导出筛选状态", str(default), "JSON (*.json)")
        if not f:
            return
        if self.export_to(f):
            QMessageBox.information(self, "导出成功", f"筛选状态已导出到：\n{f}")

    def import_state(self, file_path) -> bool:
        """从 JSON 导入筛选状态；数据集根目录不匹配时拒绝。"""
        self._begin_load("正在导入筛选状态 …")
        if self.root is None:
            QMessageBox.warning(self, "提示", "请先通过「选择数据集」设置数据集根目录，再导入。")
            self._update_counts()
            return False
        try:
            data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"读取 JSON 失败：{e}")
            self._update_counts()
            return False
        rec_root = ((data.get("统计信息") or {}).get("数据集根目录绝对路径") or "").strip()

        def _norm(p):
            try:
                return os.path.normcase(str(Path(p).resolve()))
            except Exception:
                return ""

        if not rec_root or _norm(rec_root) != _norm(self.root):
            QMessageBox.warning(
                self, "拒绝导入",
                f"数据集根目录不匹配，已拒绝导入。\n\nJSON 记录：{rec_root or '（空）'}\n当前设置：{self.root}")
            return False

        def as_list(v):
            return [str(x) for x in v] if isinstance(v, (list, tuple)) else []

        sel = [p for p in as_list(data.get("选中区")) if p in self.groups]
        exc = [p for p in as_list(data.get("排除区")) if p in self.groups]
        unf = [p for p in as_list(data.get("未筛选区")) if p in self.groups]
        missing = (len(as_list(data.get("选中区"))) - len(sel)
                   + len(as_list(data.get("排除区"))) - len(exc)
                   + len(as_list(data.get("未筛选区"))) - len(unf))
        if missing:
            r = QMessageBox.question(self, "存在缺失",
                                     f"JSON 中有 {missing} 个组在当前数据集中不存在，将被忽略。是否继续导入？")
            if r != QMessageBox.StandardButton.Yes:
                self._update_counts()
                return False
        self.selected = sel
        self.excluded = exc
        known = set(sel) | set(exc) | set(unf)
        rest = [p for p in self.groups if p not in known]
        self.unfiltered_blocks = OrderedDict()
        for p in unf + rest:
            self.unfiltered_blocks.setdefault(self.groups[p].block, []).append(p)
        for b in self.blocks:
            self.unfiltered_blocks.setdefault(b, [])
        self.history.clear()
        self._rebuild_all()
        self._update_counts()
        self._notify("导入成功")
        return True

    def _import_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "导入筛选状态", str(FILTERING_DIR), "JSON (*.json)")
        if f:
            self.import_state(f)


def main():
    # 复用已有 QApplication；宿主窗口内与工具2同窗切换
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(TOOL_QSS)
    shell = cu.ToolShell("tool1")
    shell.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
