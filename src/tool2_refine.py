"""工具2：数据集二次精筛（接工具1的选中区，RGB/深度图对比删减）。

界面：左侧可折叠目录树（块 → 组），右侧工作区按“图片对”成对显示当前组的
RGB 图与深度图（不成对时允许空缺占位），该组图片**全部显示**（不抽样）。

功能：选择数据集（全量加载）、导入 JSON（工具1“选中区”构建，或工具2自身导出
完整复原）、导出精筛状态 JSON、拷贝保留图片（按原路径结构，不改动原数据集）。

快捷键：
    空格  放大查看当前块最上方组的第一个图片对（RGB/深度并排、共享缩放）
          放大查看中：A 上一对 · D 下一对 · W 排除当前图片对 · S 恢复当前图片对 · Q/Esc 退出
          （单边排除用两侧图片下方的按钮）
"""
from __future__ import annotations

import json
import shutil
import sys
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QMenu, QMessageBox, QProgressDialog, QPushButton, QScrollArea, QSizePolicy,
                               QSplitter, QStatusBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import common_ui as cu

PROJECT_DIR = Path(__file__).resolve().parent.parent  # 项目根目录（脚本位于 src/ 内）
FILTERING_DIR = PROJECT_DIR / "filtering"             # 工作目录：状态JSON 默认读写处
ORIGINAL_DATA_DIR = FILTERING_DIR / "original_data"   # 原始数据集存放目录
COPY_DATA_DIR = FILTERING_DIR / "data"                # 最终筛选拷贝输出目录


def _ensure_work_dirs():
    for d in (FILTERING_DIR, ORIGINAL_DATA_DIR, COPY_DATA_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
THUMB = 190
CARD_W = 2 * THUMB + 6 + 16   # 图片对卡片宽度：双缩略图 + 间距 + 内边距（工作区多列排布）

ROLE_KIND = Qt.ItemDataRole.UserRole       # "block" / "group" / "excluded_group"
ROLE_PATH = Qt.ItemDataRole.UserRole + 1   # 组绝对路径（str）

TOOL_QSS = """
QMainWindow, QDialog, QMenu { background:#0D1117; color:#E6EDF3; }
QMessageBox QLabel { color:#E6EDF3; background:transparent; }
QToolTip { background:#1A212B; color:#E6EDF3; border:1px solid #30363D; }
QFrame#TopBar { background:#161B22; border-bottom:1px solid #21262D; }
QFrame#SidePanel { background:#161B22; border:1px solid #30363D; border-radius:10px; }
QLabel#SideTitle { font-weight:600; color:#F0F6FC; font-size:13px; }
QTreeWidget { border:none; background:transparent; color:#E6EDF3; }
QTreeWidget::item { padding:2px; }
QTreeWidget::item:hover { background:#1A212B; }
QTreeWidget::item:selected { background:#1F6FEB; color:#FFFFFF; }
QFrame#WorkPanel { background:#161B22; border:1px solid #30363D; border-radius:10px; }
QScrollArea#WorkScroll { border:none; background:transparent; }
QWidget#WorkContent { background:transparent; }
QLabel#wsTitle { font-weight:600; color:#F0F6FC; font-size:14px; }
QLabel#wsStats { color:#8B949E; }
QLabel#bannerExcl { background:#3D1D20; color:#F85149; border:1px solid #B62324; border-radius:6px; padding:6px 10px; }
QFrame#pairCard { background:#1A212B; border:1px solid #30363D; border-radius:8px; }
QFrame#pairCard[excl="true"] { border-color:#B62324; background:#231316; }
QFrame#pairCard[flash="true"] { background:#4A3808; border-color:#E3B341; }
QLabel#pairName { font-weight:600; color:#F0F6FC; }
QLabel#pairStatus { color:#8B949E; }
QLabel#paneCaption { color:#8B949E; }
QFrame#pairPane { background:transparent; }
QFrame#pairPane[excl="true"] { border:2px solid #F85149; border-radius:6px; }
QLabel#missing { color:#6E7681; background:#10151C; border:1px dashed #30363D; border-radius:6px; }
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


# ---------------------------------------------------------------- 数据模型

_STRIP_SUFFIXES = ("_color", "_rgb", "_depth", "_dep", "_ir")


def normalize_stem(stem: str) -> str:
    low = stem.lower()
    for suf in _STRIP_SUFFIXES:
        if low.endswith(suf) and len(low) > len(suf):
            return stem[:-len(suf)]
    return stem


@dataclass
class Pair:
    name: str
    rgb: Path = None      # 缺失为 None
    depth: Path = None


@dataclass
class GroupData:
    path: Path
    rel: str
    block: str
    rgb: list
    depth: list
    pairs: list = field(default_factory=list)
    exc_rgb: set = field(default_factory=set)
    exc_depth: set = field(default_factory=set)
    exc_whole: bool = False

    @classmethod
    def build(cls, path: Path, rel: str, block: str, rgb: list, depth: list):
        gd = cls(path, rel, block, rgb, depth)
        gd.pairs = build_pairs(rgb, depth)
        return gd

    def rgb_retained(self):
        return [] if self.exc_whole else [p for p in self.rgb if p not in self.exc_rgb]

    def depth_retained(self):
        return [] if self.exc_whole else [p for p in self.depth if p not in self.exc_depth]

    def remaining(self) -> bool:
        return bool(self.rgb_retained() or self.depth_retained())

    def restore_all(self):
        self.exc_rgb.clear()
        self.exc_depth.clear()
        self.exc_whole = False

    def clear_whole_keep_excl(self):
        """取消“整组排除”标记，但把其余图片固化为单独排除（供恢复单张/单对时使用）。"""
        if not self.exc_whole:
            return
        self.exc_whole = False
        self.exc_rgb.update(self.rgb)
        self.exc_depth.update(self.depth)


def build_pairs(rgb, depth) -> list:
    """按归一化文件名（去 color/rgb/depth 等后缀）配对；不成对的允许单边。"""
    dmap = OrderedDict()
    for p in depth:
        dmap.setdefault(normalize_stem(p.stem), p)
    used_depth = set()
    used_names = set()
    pairs = []
    for r in rgb:
        k = normalize_stem(r.stem)
        d = dmap.get(k)
        if d is not None and id(d) not in used_depth:
            used_depth.add(id(d))
            name = k
            i = 2
            while name in used_names:
                name = f"{k}#{i}"
                i += 1
            used_names.add(name)
            pairs.append(Pair(name, r, d))
        else:
            name = k
            i = 2
            while name in used_names:
                name = f"{k}#{i}"
                i += 1
            used_names.add(name)
            pairs.append(Pair(name, r, None))
    for k, d in dmap.items():
        if id(d) not in used_depth:
            name = k
            i = 2
            while name in used_names:
                name = f"{k}#{i}"
                i += 1
            used_names.add(name)
            pairs.append(Pair(name, None, d))
    return pairs


def build_group_data(scene: Path, root: Path | None) -> GroupData:
    """由场景目录构建 GroupData（路径不在 root 下时退化为全路径表示）。"""
    scene = Path(scene).resolve()
    try:
        rel = scene.relative_to(root).as_posix()
    except (ValueError, TypeError):
        rel = scene.as_posix()
    block = rel.split("/", 1)[0] if "/" in rel else rel
    return GroupData.build(scene, rel, block,
                           cu.list_images(scene / "color"), cu.list_images(scene / "depth"))


def common_root(paths) -> Path:
    """推测一组绝对路径的公共上级目录。"""
    parts_list = [Path(p).parts for p in paths if str(p)]
    if not parts_list:
        raise ValueError("empty")
    common = list(parts_list[0])
    for parts in parts_list[1:]:
        i = 0
        while i < min(len(common), len(parts)) and common[i] == parts[i]:
            i += 1
        common = common[:i]
    while common and not Path(*common).is_dir():
        common = common[:-1]
    if not common:
        raise ValueError("no common root")
    return Path(*common)


# ---------------------------------------------------------------- 拷贝

def collect_copy_files(groups: "OrderedDict[str, GroupData]"):
    """[(源文件绝对路径, 相对数据集根目录的 posix 路径)]。"""
    files = []
    for gd in groups.values():
        if not gd.remaining():
            continue
        for p in (*gd.rgb_retained(), *gd.depth_retained()):
            files.append((p, f"{gd.rel}/{p.parent.name}/{p.name}"))
    return files


def perform_copy(files, dest_root: Path, progress=None, cancel=None):
    """同步拷贝核心：返回 (成功数, 错误列表)。progress(done, total)。"""
    dest_root = Path(dest_root)
    done, errs = 0, []
    total = len(files)
    for src, rel in files:
        if cancel is not None and cancel():
            break
        try:
            dst = dest_root / Path(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            done += 1
        except Exception as e:
            errs.append(f"{src} -> {e}")
        if progress is not None:
            progress(done, total)
    return done, errs


class _CopySignals(QObject):
    progress = Signal(int, int)
    finished = Signal(int, list)


class _CopyJob(QRunnable):
    def __init__(self, files, dest, signals: _CopySignals):
        super().__init__()
        self._files = files
        self._dest = dest
        self._signals = signals
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        def prog(d, t):
            self._signals.progress.emit(d, t)

        done, errs = perform_copy(self._files, self._dest, prog, lambda: self._cancel)
        self._signals.finished.emit(done, errs)


class CopyDialog(QProgressDialog):
    """带进度条的后台拷贝对话框。"""

    def __init__(self, parent, files, dest):
        super().__init__("正在拷贝文件…", "取消", 0, len(files), parent)
        self.setWindowTitle("拷贝保留图片")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumDuration(0)
        self._signals = _CopySignals()
        self._signals.progress.connect(self.setValue)
        self._signals.finished.connect(self._on_finished)
        self._job = _CopyJob(files, dest, self._signals)
        self.canceled.connect(self._job.cancel)
        QThreadPool.globalInstance().start(self._job)
        self.show()

    def _on_finished(self, done, errs):
        self.setMaximum(1)
        self.setValue(1)
        self.reset()
        dest_txt = f"（{done} 张成功"
        if errs:
            dest_txt += f"，{len(errs)} 张失败）"
            tip = "\n".join(errs[:8])
            QMessageBox.warning(self.parent(), "拷贝完成（部分失败）",
                                f"{dest_txt}\n失败示例：\n{tip}")
        else:
            QMessageBox.information(self.parent(), "拷贝完成", f"已拷贝 {done} 张图片。")


# ---------------------------------------------------------------- 工作区卡片

class PairCard(QFrame):
    """一个图片对卡片：名称 + 状态 + 排除按钮 + RGB/深度两栏缩略图。"""

    def __init__(self, gd: GroupData, pair: Pair, manager, on_change, on_zoom=None, parent=None):
        super().__init__(parent)
        self.gd = gd
        self.pair = pair
        self.on_change = on_change
        self.on_zoom = on_zoom
        self.setObjectName("pairCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(4)
        self.setFixedWidth(CARD_W)

        # 头行：编号 + 状态（左对齐紧随编号）…… 排除图片对（右上角）
        head = QHBoxLayout()
        kind = "图片对" if (pair.rgb is not None and pair.depth is not None) else \
               ("仅RGB图" if pair.rgb is not None else "仅深度图")
        name = QLabel(f"{kind}：{pair.name}")
        name.setObjectName("pairName")
        self.status = QLabel("")
        self.status.setObjectName("pairStatus")
        self.btn_pair = QPushButton("排除图片对")
        self.btn_pair.setObjectName("chipExclude")
        head.addWidget(name)
        head.addWidget(self.status)
        head.addStretch(1)
        head.addWidget(self.btn_pair)
        lay.addLayout(head)

        # 主体：RGB/深度两栏，各自的排除按钮位于对应图片正下方
        self.btn_rgb = QPushButton("排除RGB图")
        self.btn_dep = QPushButton("排除深度图")
        body = QHBoxLayout()
        body.setSpacing(6)
        self.pane_rgb = self._make_pane(manager, pair.rgb, False, "RGB", self.btn_rgb)
        self.pane_depth = self._make_pane(manager, pair.depth, True, "深度", self.btn_dep)
        body.addWidget(self.pane_rgb, 0, Qt.AlignmentFlag.AlignHCenter)
        body.addWidget(self.pane_depth, 0, Qt.AlignmentFlag.AlignHCenter)
        body.addStretch(1)
        lay.addLayout(body)

        self.btn_pair.clicked.connect(lambda: self._toggle_pair())
        self.btn_rgb.clicked.connect(lambda: self._toggle_single(False))
        self.btn_dep.clicked.connect(lambda: self._toggle_single(True))
        for b in (self.btn_pair, self.btn_rgb, self.btn_dep):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 避免按钮抢占空格快捷键
        self._refresh()

    def _make_pane(self, manager, path, depth_mode, caption, button=None):
        box = QFrame()
        box.setObjectName("pairPane")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        if path is not None:
            strip = cu.ThumbStrip(manager, [(path, depth_mode)], THUMB)
            # 深色底衬：图片居中显示在 190×190 色块上，与缺图占位框高度视觉一致
            strip.setStyleSheet("QLabel{background:#10151C;border-radius:4px;}")
            # 点击/右键查看 → 与空格一致的图片对浏览界面（定位到该对）
            strip.viewRequested.connect(lambda p, d: (self.on_zoom(self.pair) if self.on_zoom else None))
            v.addWidget(strip, 0)
            cap = QLabel(f"{caption} · {Path(path).name}")
        else:
            miss = QLabel(f"（无{caption}图）")
            miss.setObjectName("missing")
            miss.setAlignment(Qt.AlignmentFlag.AlignCenter)
            miss.setFixedSize(THUMB, THUMB)
            v.addWidget(miss, 0, Qt.AlignmentFlag.AlignHCenter)
            cap = QLabel(f"{caption} · 无")
        cap.setObjectName("paneCaption")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(cap)
        if button is not None:
            v.addWidget(button)   # 单边排除按钮：位于该侧图片正下方
        return box

    def _rgb_excl(self):
        return self.pair.rgb is not None and (self.gd.exc_whole or self.pair.rgb in self.gd.exc_rgb)

    def _depth_excl(self):
        return self.pair.depth is not None and (self.gd.exc_whole or self.pair.depth in self.gd.exc_depth)

    def _toggle_pair(self):
        if self._rgb_excl() and self._depth_excl():   # 恢复整对
            self.gd.clear_whole_keep_excl()
            if self.pair.rgb is not None:
                self.gd.exc_rgb.discard(self.pair.rgb)
            if self.pair.depth is not None:
                self.gd.exc_depth.discard(self.pair.depth)
        else:                                          # 排除整对
            if self.pair.rgb is not None:
                self.gd.exc_rgb.add(self.pair.rgb)
            if self.pair.depth is not None:
                self.gd.exc_depth.add(self.pair.depth)
        self._changed()

    def _toggle_single(self, depth_mode: bool):
        p = self.pair.depth if depth_mode else self.pair.rgb
        if p is None:
            return
        exc = self.gd.exc_depth if depth_mode else self.gd.exc_rgb
        if p in exc or self.gd.exc_whole:   # 恢复单张
            self.gd.clear_whole_keep_excl()
            exc.discard(p)
        else:                               # 排除单张
            exc.add(p)
        self._changed()

    def _changed(self):
        self._refresh()
        self.on_change()

    def _refresh(self):
        r, d = self._rgb_excl(), self._depth_excl()
        both = self.pair.rgb is not None and self.pair.depth is not None
        if self.gd.exc_whole:
            status, color = "整组已排除", "#F85149"
        elif r and d:
            status, color = "已排除（对）", "#F85149"
        elif r or d:
            status, color = "已排除（单边）", "#F85149"
        elif both:
            status, color = "完整图片对", "#3FB950"
        else:
            status, color = "单边（无配对）", "#D29922"
        self.status.setText(status)
        self.status.setStyleSheet(f"color:{color};font-weight:600;")
        any_retained = (self.pair.rgb is not None and not r) or (self.pair.depth is not None and not d)
        self.btn_pair.setText("恢复图片对" if (r and d) else "排除图片对")
        self.btn_pair.setObjectName("chipRestore" if (r and d) else "chipExclude")
        cu.restyle(self.btn_pair)
        self.btn_rgb.setText("恢复RGB图" if r else "排除RGB图")
        self.btn_rgb.setObjectName("chipRestore" if r else "chipExclude")
        self.btn_rgb.setEnabled(self.pair.rgb is not None)
        cu.restyle(self.btn_rgb)
        self.btn_dep.setText("恢复深度图" if d else "排除深度图")
        self.btn_dep.setObjectName("chipRestore" if d else "chipExclude")
        self.btn_dep.setEnabled(self.pair.depth is not None)
        cu.restyle(self.btn_dep)
        self.setProperty("excl", "true" if (r and d) else "false")
        cu.restyle(self)
        for pane, excl in ((self.pane_rgb, r), (self.pane_depth, d)):
            # 单边排除：红框 + 半透明变暗，视觉反馈与整对排除一致
            pane.setProperty("excl", "true" if excl else "false")
            cu.restyle(pane)
            eff = pane.graphicsEffect()
            if excl and eff is None:
                eff = QGraphicsOpacityEffect(pane)
                eff.setOpacity(0.35)
                pane.setGraphicsEffect(eff)
            elif not excl and eff is not None:
                pane.setGraphicsEffect(None)


# ---------------------------------------------------------------- 放大浏览

class ZoomWalkDialog(QDialog):
    """图片对放大浏览：空格进入，或点击缩略图定位进入；RGB/深度并排、共享缩放。

    A/D 上一对/下一对；W 排除当前图片对（已全排除时为恢复）；S 恢复当前图片对；
    单边排除通过两侧图片下方的按钮操作。
    """

    def __init__(self, parent, gd: GroupData, on_toggle_image, start_index: int = 0):
        super().__init__(parent)
        self.gd = gd
        self.pairs = [p for p in gd.pairs if p.rgb is not None or p.depth is not None]
        self.on_toggle_image = on_toggle_image
        self.idx = start_index % len(self.pairs) if self.pairs else 0
        self.setWindowTitle(f"放大查看 - {gd.rel}")
        self.resize(1360, 840)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        self.caption = QLabel("")
        self.caption.setWordWrap(True)
        lay.addWidget(self.caption)
        self.viewer = cu.PairViewWidget()
        lay.addWidget(self.viewer, 1)

        # 单边排除/复制按钮（挂在两侧图片下方；复制按钮在点击时读取当前对）
        self.b_rgb = QPushButton("")
        self.b_rgb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_rgb.clicked.connect(lambda: self._toggle_side(False))
        self.b_rgb_copy = QPushButton("复制RGB")
        self.b_rgb_copy.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_rgb_copy.clicked.connect(
            lambda: self._copy_current(False))
        self.viewer.box_rgb.show_buttons(self.b_rgb, self.b_rgb_copy)
        self.b_dep = QPushButton("")
        self.b_dep.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_dep.clicked.connect(lambda: self._toggle_side(True))
        self.b_dep_copy = QPushButton("复制深度")
        self.b_dep_copy.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_dep_copy.clicked.connect(
            lambda: self._copy_current(True))
        self.viewer.box_depth.show_buttons(self.b_dep, self.b_dep_copy)

        btns = QHBoxLayout()
        b_prev = QPushButton("上一对 (A)")
        b_next = QPushButton("下一对 (D)")
        self.b_pair = QPushButton("")
        self.b_pair.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b_quit = QPushButton("退出 (Q)")
        btns.addWidget(b_prev)
        btns.addWidget(b_next)
        btns.addWidget(self.b_pair)
        btns.addStretch(1)
        btns.addWidget(b_quit)
        lay.addLayout(btns)
        hint = QLabel("A 上一对 · D 下一对 · W 排除当前图片对 · S 恢复当前图片对 · Q/Esc 退出｜滚轮缩放（左右同步）")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color:#8B949E;")
        lay.addWidget(hint)
        b_prev.clicked.connect(lambda: self._step(-1))
        b_next.clicked.connect(lambda: self._step(1))
        self.b_pair.clicked.connect(self._toggle_pair)
        b_quit.clicked.connect(self.accept)
        for key, fn in (("A", lambda: self._step(-1)), ("D", lambda: self._step(1)),
                        ("Left", lambda: self._step(-1)), ("Right", lambda: self._step(1)),
                        ("W", self._toggle_pair), ("S", self._restore_pair), ("Q", self.accept)):
            QShortcut(QKeySequence(key), self).activated.connect(fn)
        self._show_idx()

    # ---- 排除状态查询
    def _side_excl(self, depth_mode: bool) -> bool:
        pr = self.pairs[self.idx]
        p = pr.depth if depth_mode else pr.rgb
        if p is None:
            return False
        return self.gd.exc_whole or (p in (self.gd.exc_depth if depth_mode else self.gd.exc_rgb))

    def _copy_current(self, depth_mode: bool):
        pr = self.pairs[self.idx]
        p = pr.depth if depth_mode else pr.rgb
        if p is not None:
            cu.copy_image_to_clipboard(str(p))

    # ---- 操作
    def _step(self, delta):
        if not self.pairs:
            return
        self.idx = (self.idx + delta) % len(self.pairs)
        self._show_idx()

    def _apply_pair_excl(self, excl: bool):
        pr = self.pairs[self.idx]
        if excl:
            if pr.rgb is not None:
                self.gd.exc_rgb.add(pr.rgb)
            if pr.depth is not None:
                self.gd.exc_depth.add(pr.depth)
        else:
            self.gd.clear_whole_keep_excl()
            if pr.rgb is not None:
                self.gd.exc_rgb.discard(pr.rgb)
            if pr.depth is not None:
                self.gd.exc_depth.discard(pr.depth)
        self.on_toggle_image()   # 通知主窗口同步（树/统计/工作区）

    def _toggle_pair(self):
        any_retained = ((self.pairs[self.idx].rgb is not None and not self._side_excl(False))
                        or (self.pairs[self.idx].depth is not None and not self._side_excl(True)))
        self._apply_pair_excl(bool(any_retained))
        self._show_idx()

    def _restore_pair(self):
        self._apply_pair_excl(False)
        self._show_idx()

    def _toggle_side(self, depth_mode: bool):
        pr = self.pairs[self.idx]
        p = pr.depth if depth_mode else pr.rgb
        if p is None:
            return
        if self._side_excl(depth_mode):
            self.gd.clear_whole_keep_excl()
            (self.gd.exc_depth if depth_mode else self.gd.exc_rgb).discard(p)
        else:
            (self.gd.exc_depth if depth_mode else self.gd.exc_rgb).add(p)
        self.on_toggle_image()
        self._show_idx()

    # ---- 显示
    def _show_idx(self):
        pr = self.pairs[self.idx]
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rgb_img = cu.load_qimage(pr.rgb, 2200, False) if pr.rgb is not None else None
            depth_img = cu.load_qimage(pr.depth, 2200, True) if pr.depth is not None else None
        finally:
            QApplication.restoreOverrideCursor()
        self.viewer.set_images(
            rgb_img, depth_img,
            f"RGB · {pr.rgb.name}" if pr.rgb is not None else "RGB · 无",
            f"深度 · {pr.depth.name}（已归一化）" if pr.depth is not None else "深度 · 无")

        r_excl, d_excl = self._side_excl(False), self._side_excl(True)
        self.viewer.box_rgb.set_excluded(r_excl)
        self.viewer.box_depth.set_excluded(d_excl)
        self.b_rgb.setText("恢复RGB (已排除)" if r_excl else "排除RGB")
        self.b_rgb.setObjectName("chipRestore" if r_excl else "chipExclude")
        cu.restyle(self.b_rgb)
        self.b_dep.setText("恢复深度 (已排除)" if d_excl else "排除深度")
        self.b_dep.setObjectName("chipRestore" if d_excl else "chipExclude")
        cu.restyle(self.b_dep)

        kind = "图片对" if (pr.rgb is not None and pr.depth is not None) else \
               ("仅RGB" if pr.rgb is not None else "仅深度")
        tag = ""
        if r_excl and d_excl:
            tag = "【整对已排除】"
        elif r_excl or d_excl:
            tag = "【单边已排除】"
        self.caption.setText(f"{self.idx + 1} / {len(self.pairs)}　{kind}：{pr.name}　{tag}")
        self.caption.setStyleSheet("font-weight:600;color:#F85149;" if tag else "font-weight:600;")
        any_retained = ((pr.rgb is not None and not r_excl) or (pr.depth is not None and not d_excl))
        self.b_pair.setText("恢复当前图片对 (S)" if not any_retained else "排除当前图片对 (W)")
        self.b_pair.setObjectName("chipRestore" if not any_retained else "chipExclude")
        cu.restyle(self.b_pair)


# ---------------------------------------------------------------- 主窗口

class MainWindow(QWidget):
    """工具2页面（可独立运行，也可嵌入 ToolShell 与工具1同窗切换）。"""

    switchRequested = Signal(str)   # 请求宿主切换到另一工具

    def __init__(self):
        super().__init__()
        self.resize(1560, 920)
        _ensure_work_dirs()
        self.manager = cu.ThumbManager(self)

        self.root = None
        self.groups = OrderedDict()   # str(组绝对路径) -> GroupData
        self.img_owner = {}           # str(图片绝对路径) -> 组绝对路径
        self.current_group = None

        self._tree_blocks = {}        # 块名 -> QTreeWidgetItem
        self._tree_groups = {}        # 组路径 -> QTreeWidgetItem
        self._excl_node = None
        self._syncing_tree = False

        self._build_ui()

    # ------------------------------------------------ UI 构建
    def _build_ui(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        hlay = QHBoxLayout(bar)
        hlay.setContentsMargins(10, 6, 10, 6)
        hlay.setSpacing(8)
        self.btn_dataset = QPushButton("选择数据集")
        self.btn_dataset.setObjectName("btnPrimary")
        self.btn_import = QPushButton("导入")
        self.btn_export = QPushButton("导出")
        self.btn_copy = QPushButton("拷贝")
        self.btn_switch = QPushButton("→ 工具1")
        self.btn_switch.setToolTip("同窗切回工具1（组级初筛）：数据集保持一致，两侧工作状态各自保留")
        for b in (self.btn_dataset, self.btn_import, self.btn_export, self.btn_copy, self.btn_switch):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 避免按钮抢占空格快捷键
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)  # 宽度贴合文字，不随布局拉伸
        self.btn_import.setToolTip("导入工具1或工具2导出的JSON：前者按“选中区”构建，后者完整复原精筛状态")
        self.btn_copy.setToolTip("把当前保留的图片按原路径结构拷贝一份（不改动原数据集），默认目标：filtering/data")
        # 布局：[选择数据集][拷贝 导入 导出] ……弹簧…… [→工具1]（数据集路径与统计显示在左下角状态栏）
        hlay.addWidget(self.btn_dataset)
        hlay.addWidget(self.btn_copy)
        hlay.addWidget(self.btn_import)
        hlay.addWidget(self.btn_export)
        hlay.addStretch(1)
        hlay.addWidget(self.btn_switch)   # 切换按钮最右

        # 左：目录树
        side = QFrame()
        side.setObjectName("SidePanel")
        sv = QVBoxLayout(side)
        sv.setContentsMargins(8, 8, 8, 8)
        sv.setSpacing(6)
        st = QLabel("数据集目录树（块 / 组）")
        st.setObjectName("SideTitle")
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        sv.addWidget(st)
        sv.addWidget(self.tree, 1)

        # 右：工作区
        work = QFrame()
        work.setObjectName("WorkPanel")
        wv = QVBoxLayout(work)
        wv.setContentsMargins(8, 8, 8, 8)
        wv.setSpacing(6)
        head = QHBoxLayout()
        self.ws_title = cu.ElidedLabel("未选择组")
        self.ws_title.setObjectName("wsTitle")
        self.ws_title.setMinimumWidth(200)
        self.ws_stats = QLabel("")
        self.ws_stats.setObjectName("wsStats")
        self.btn_group_excl = QPushButton("排除整组")
        self.btn_group_excl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_group_excl.setObjectName("chipExclude")
        self.btn_group_excl.setToolTip("排除整组后，组会从目录树移到「已排除组」下，可随时恢复（恢复将还原该组全部排除状态）")
        head.addWidget(self.ws_title, 1)
        head.addWidget(self.ws_stats)
        head.addWidget(self.btn_group_excl)
        wv.addLayout(head)
        self.banner = QLabel("⚠ 该组已被完全排除（无保留图片）")
        self.banner.setObjectName("bannerExcl")
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.hide()
        wv.addWidget(self.banner)
        self.ws_scroll = QScrollArea()
        self.ws_scroll.setObjectName("WorkScroll")
        self.ws_scroll.setWidgetResizable(True)
        self.ws_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.ws_content = QWidget()
        self.ws_content.setObjectName("WorkContent")
        # 流式多列布局：按工作区宽度自动排布 2~N 列图片对卡片，提高空间利用率
        self.ws_lay = cu.FlowLayout(self.ws_content, margin=2, hspacing=12, vspacing=12)
        self.ws_scroll.setWidget(self.ws_content)
        wv.addWidget(self.ws_scroll, 1)

        split = QSplitter()
        split.addWidget(side)
        split.addWidget(work)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 1200])   # 侧边栏默认宽度与工具1“选中区”一致
        side.setMinimumWidth(240)
        side.setMaximumWidth(560)

        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(8, 8, 8, 8)
        cv.setSpacing(8)
        cv.addWidget(bar)
        cv.addWidget(split, 1)

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
        help_lbl = QLabel("空格 放大查看图片对（A/D 换对 · W/S 排除/恢复 · Q 退出）｜缩略图左键查看图片对 · 右键复制")
        help_lbl.setStyleSheet("color:#8B949E;")
        self.statusBar().addPermanentWidget(help_lbl)

        self.btn_dataset.clicked.connect(self._choose_dataset)
        self.btn_import.clicked.connect(self._import_dialog)
        self.btn_export.clicked.connect(self._export_dialog)
        self.btn_copy.clicked.connect(self._copy_dialog)
        self.btn_switch.clicked.connect(self._switch_to_tool1)
        self.btn_group_excl.clicked.connect(self._toggle_group_excluded)
        self.tree.itemSelectionChanged.connect(self._on_tree_sel)
        # 快捷键限定在本页面内生效（同窗双页面下避免串扰）
        sc = QShortcut(QKeySequence("Space"), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._hk_zoom)

    def statusBar(self) -> QStatusBar:
        """页面内嵌状态栏（页面嵌入 ToolShell 时随页面显示）。"""
        return self._statusbar

    def _notify(self, msg: str, msec: int = 4000):
        """底部短通知（独立标签，与统计信息互不遮挡）。"""
        self.notify_lbl.setText(msg)
        self._notify_timer.start(msec)

    def _begin_load(self, msg: str):
        """开始加载/导入新数据集：立即清除旧数据集的全部信息（含残留通知）。"""
        self._notify_timer.stop()
        self.notify_lbl.setText("")
        self.status_lbl.setText(msg)
        QApplication.processEvents()

    # ------------------------------------------------ 工具切换
    def _switch_to_tool1(self):
        """同窗切换回工具1：交接数据集根目录；两侧工作状态各自保留。"""
        self._pending_handoff = {"root": str(self.root) if self.root else None}
        self.switchRequested.emit("tool1")

    def apply_handoff(self, root, selected=None):
        """切换接续：数据集相同→保留精筛状态；不同→立即清除旧数据集并按新数据集重建。"""
        if not root:
            return
        if self.root is not None and str(self.root) == str(root):
            return   # 同一数据集：保留精筛状态
        self._begin_load(f"正在接续数据集：{root} …")
        try:
            sel = [p for p in (selected or []) if Path(p).is_dir()]
            if sel and self.load_groups_from_paths(Path(root), sel):
                self._after_load(f"已接续工具1选中区：{len(self.groups)} 组")
            else:
                self.load_dataset(Path(root))
        except Exception:
            traceback.print_exc()

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

    # ------------------------------------------------ 数据载入
    def _choose_dataset(self):
        d = QFileDialog.getExistingDirectory(self, "选择数据集根目录", str(FILTERING_DIR))
        if not d:
            return
        self.load_dataset(Path(d))

    def load_dataset(self, root: Path):
        self._begin_load(f"正在扫描数据集：{root} …")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            groups = cu.scan_dataset(root)
        finally:
            QApplication.restoreOverrideCursor()
        if not groups:
            QMessageBox.warning(self, "未发现数据",
                                "该目录下未发现符合结构的数据（需存在包含 color/ 或 depth/ 子目录的场景目录）。")
            self._update_nav_stats()
            return
        self.root = Path(root).resolve()
        self.groups = OrderedDict((str(g.path),
                                   GroupData.build(g.path, g.rel, g.block, list(g.rgb), list(g.depth)))
                                  for g in groups)
        self._after_load()

    def import_json(self, file_path) -> bool:
        """导入 JSON：自动识别 工具1格式（未筛选区/选中区/排除区）或 工具2格式（图片对/仅RGB图/仅深度图）。

        - 工具1格式：按“选中区”构建目录树（初始无排除）；
        - 工具2格式：按导出记录完整复原筛选状态（未列出的图片即视为已排除）。
        """
        self._begin_load("正在导入筛选状态 …")
        try:
            data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"读取 JSON 失败：{e}")
            self._update_nav_stats()
            return False
        is_own = any(k in data for k in ("图片对", "仅RGB图", "仅深度图"))
        if is_own:
            return self._import_own_format(data)
        sel = [str(x) for x in (data.get("选中区") or [])] if isinstance(data.get("选中区"), (list, tuple)) else []
        if not sel:
            QMessageBox.warning(self, "导入失败", "JSON 中“选中区”为空，无法构建目录树。")
            self._update_nav_stats()
            return False
        rec_root = ((data.get("统计信息") or {}).get("数据集根目录绝对路径") or "").strip()
        try:
            root = Path(rec_root).resolve() if rec_root else common_root(sel)
        except Exception:
            root = None
        if not self.load_groups_from_paths(root, sel):
            QMessageBox.warning(self, "导入失败", "JSON“选中区”内的路径在磁盘上均不存在。")
            self._update_nav_stats()
            return False
        self._after_load("导入成功，已按选中区构建目录树")
        return True

    def load_groups_from_paths(self, root, group_paths) -> bool:
        """按组路径列表构建目录树（工具1“选中区”/工具切换交接共用）。"""
        self._begin_load("正在构建目录树 …")
        groups = OrderedDict()
        for p in group_paths:
            d = Path(p)
            if not d.is_dir():
                continue
            gd = build_group_data(d, root)
            groups[str(gd.path)] = gd
        if not groups:
            return False
        self.root = root
        self.groups = groups
        return True

    def _import_own_format(self, data: dict) -> bool:
        """导入工具2自己导出的 JSON，复原完整筛选状态。"""
        def as_list(v):
            return [str(x) for x in v] if isinstance(v, (list, tuple)) else []

        pair_rgb = set(as_list(data.get("图片对")))       # 完整图片对的RGB路径
        only_rgb = set(as_list(data.get("仅RGB图")))
        only_depth = set(as_list(data.get("仅深度图")))
        all_retained_rgb = pair_rgb | only_rgb
        rec_root = ((data.get("统计信息") or {}).get("数据集根目录绝对路径") or "").strip()
        group_paths = as_list(data.get("全部组"))
        if not group_paths:
            # 兼容无“全部组”字段的导出：从图片路径反推组目录（color/depth 的上一级）
            derived = {str(Path(p).parent.parent) for p in (all_retained_rgb | only_depth)}
            group_paths = sorted(derived)
        if not group_paths:
            QMessageBox.warning(self, "导入失败", "JSON 中没有任何可用的组或图片路径。")
            self._update_nav_stats()
            return False
        try:
            root = Path(rec_root).resolve() if rec_root else common_root(group_paths)
        except Exception:
            root = None
        groups = OrderedDict()
        missing = []
        for gp in group_paths:
            d = Path(gp)
            if not d.is_dir():
                missing.append(gp)
                continue
            gd = build_group_data(d, root)
            # 复原排除状态：按导出时各图片的归属，未列出的即排除
            for pr in gd.pairs:
                if pr.rgb is not None and str(pr.rgb) in pair_rgb:
                    pass  # 完整保留
                elif pr.rgb is not None and str(pr.rgb) in only_rgb:
                    if pr.depth is not None:
                        gd.exc_depth.add(pr.depth)   # 深度被排除 → 仅RGB
                elif pr.depth is not None and str(pr.depth) in only_depth:
                    if pr.rgb is not None:
                        gd.exc_rgb.add(pr.rgb)       # RGB被排除 → 仅深度
                else:
                    if pr.rgb is not None:
                        gd.exc_rgb.add(pr.rgb)
                    if pr.depth is not None:
                        gd.exc_depth.add(pr.depth)
            if not gd.remaining():
                gd.exc_whole = True   # 无任何保留 → 整组排除（便于在“已排除组”中恢复）
            groups[str(gd.path)] = gd
        if not groups:
            QMessageBox.warning(self, "导入失败", "JSON 中的组路径在磁盘上均不存在。")
            self._update_nav_stats()
            return False
        if missing:
            QMessageBox.warning(self, "部分路径缺失", f"{len(missing)} 个组路径不存在，已忽略：\n" +
                                "\n".join(missing[:5]) + ("…" if len(missing) > 5 else ""))
        self.root = root
        self.groups = groups
        self._after_load("导入成功，已复原精筛状态")
        return True

    def _after_load(self, msg: str = None):
        """加载完成后的收尾；msg 仅用于简短动作反馈（不携带数据集路径，避免驻留旧信息）。"""
        self.img_owner = {}
        for gp, gd in self.groups.items():
            for p in (*gd.rgb, *gd.depth):
                self.img_owner[str(p)] = gp
        self.current_group = None
        self._rebuild_tree()
        first = next((gp for gp, gd in self.groups.items() if gd.remaining()), None)
        if first:
            self._select_group_item(first)
        else:
            self._show_group(None)
        self._update_nav_stats()
        if msg:
            self._notify(msg, 4000)

    # ------------------------------------------------ 目录树
    def _rebuild_tree(self):
        self._syncing_tree = True
        self.tree.clear()
        self._tree_blocks = {}
        self._tree_groups = {}
        self._excl_node = None
        for gp, gd in self.groups.items():
            self._ensure_group_item(gp)
        self._prune_tree()
        self.tree.expandAll()
        self._syncing_tree = False

    def _rel_wo_block(self, gd: GroupData):
        return gd.rel.split("/", 1)[1] if "/" in gd.rel else gd.rel

    def _ensure_group_item(self, gp):
        gd = self.groups[gp]
        remaining = gd.remaining()
        item = self._tree_groups.get(gp)
        if item is None:
            item = QTreeWidgetItem()
            item.setData(0, ROLE_KIND, "group")
            item.setData(0, ROLE_PATH, gp)
            self._tree_groups[gp] = item
        if remaining:
            parent = self._ensure_block_item(gd.block)
            if item.parent() is not parent:
                if item.parent() is not None:
                    item.parent().removeChild(item)
                parent.addChild(item)
            item.setText(0, f"{self._rel_wo_block(gd)}　RGB {len(gd.rgb_retained())} ｜ 深度 {len(gd.depth_retained())}")
            item.setForeground(0, QBrush(QColor("#F0F6FC")))
            item.setToolTip(0, str(gd.path))
        else:
            node = self._ensure_excl_node()
            if item.parent() is not node:
                if item.parent() is not None:
                    item.parent().removeChild(item)
                node.addChild(item)
            item.setText(0, f"{gd.rel}（已排除）")
            item.setForeground(0, QBrush(QColor("#9AA4AF")))
            item.setToolTip(0, str(gd.path))
        return item

    def _ensure_block_item(self, block):
        item = self._tree_blocks.get(block)
        if item is None:
            item = QTreeWidgetItem([f"{block}　·　剩余组 0"])
            item.setData(0, ROLE_KIND, "block")
            item.setForeground(0, QBrush(QColor("#79C0FF")))
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            self._tree_blocks[block] = item
            self.tree.addTopLevelItem(item)
        return item

    def _ensure_excl_node(self):
        if self._excl_node is None:
            self._excl_node = QTreeWidgetItem(["已排除组（0）"])
            self._excl_node.setData(0, ROLE_KIND, "excluded_root")
            self._excl_node.setForeground(0, QBrush(QColor("#F85149")))
            self.tree.addTopLevelItem(self._excl_node)
        return self._excl_node

    def _prune_tree(self):
        for block, item in list(self._tree_blocks.items()):
            n = self._count_remaining_children(item)
            if n == 0:
                parent = item.parent()
                if parent is not None:
                    parent.takeChild(parent.indexOfChild(item))
                else:
                    idx = self.tree.indexOfTopLevelItem(item)
                    if idx >= 0:
                        self.tree.takeTopLevelItem(idx)
                self._tree_blocks.pop(block, None)
            else:
                item.setText(0, f"{block}　·　剩余组 {n}")
        if self._excl_node is not None:
            n = self._excl_node.childCount()
            if n == 0:
                idx = self.tree.indexOfTopLevelItem(self._excl_node)
                if idx >= 0:
                    self.tree.takeTopLevelItem(idx)
                self._excl_node = None
            else:
                self._excl_node.setText(0, f"已排除组（{n}）")

    @staticmethod
    def _count_remaining_children(item):
        n = 0
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, ROLE_KIND) == "group":
                n += 1
        return n

    def _sync_group(self, gp):
        """组排除状态变化后同步目录树 / 工作区 / 统计。"""
        gd = self.groups[gp]
        was_current = self.current_group == gp
        self._ensure_group_item(gp)
        self._prune_tree()
        self._update_nav_stats()
        if was_current:
            self._refresh_ws_header()
            if not gd.remaining():
                self._advance_if_dead(gp)

    def _advance_if_dead(self, gp):
        keys = list(self.groups)
        try:
            i = keys.index(gp)
        except ValueError:
            i = -1
        nxt = next((k for k in keys[i + 1:] + keys[:i]
                    if self.groups[k].remaining()), None)
        if nxt:
            self._select_group_item(nxt)
        else:
            self._show_group(None)
            self._notify("所有组均已完全排除")

    def _select_group_item(self, gp):
        item = self._tree_groups.get(gp)
        if item is None:
            self._show_group(gp)
            return
        self._syncing_tree = True
        self.tree.setCurrentItem(item)
        self._syncing_tree = False
        self._show_group(gp)

    def _on_tree_sel(self):
        if self._syncing_tree:
            return
        item = self.tree.currentItem()
        if item is None:
            return
        gp = item.data(0, ROLE_PATH)
        if gp:
            self._show_group(gp)

    def _tree_menu(self, gpos):
        item = self.tree.itemAt(gpos)
        if item is None:
            return
        gp = item.data(0, ROLE_PATH)
        if not gp:
            return
        gd = self.groups[gp]
        menu = QMenu(self.tree)
        if gd.remaining():
            a = menu.addAction("排除整组")
            act = menu.exec(gpos)
            if act is a:
                self._set_group_excluded(gp, True)
        else:
            a = menu.addAction("恢复整组（还原全部排除状态）")
            act = menu.exec(gpos)
            if act is a:
                self._set_group_excluded(gp, False)

    # ------------------------------------------------ 工作区
    def _show_group(self, gp):
        self.current_group = gp
        while self.ws_lay.count():
            itm = self.ws_lay.takeAt(0)
            w = itm.widget()
            if w is not None:
                w.hide()            # 先隐藏再移除，避免重挂载瞬间在屏幕上闪现小窗口
                w.setParent(None)
                w.deleteLater()
        if gp is None or gp not in self.groups:
            self.ws_title.setText("未选择组" if self.groups else "请先「选择数据集」或「导入」JSON")
            self.ws_stats.setText("")
            self.btn_group_excl.setEnabled(False)
            self.banner.hide()
            if self.groups:
                hint = QLabel("左侧目录树已没有保留的组。完全排除的组可在「已排除组」中右键恢复。")
                hint.setStyleSheet("color:#9AA4AF;")
                self.ws_lay.addWidget(hint)
            else:
                hint = QLabel("通过上方「选择数据集」加载，或「导入」工具1导出的 JSON（按选中区构建）。")
                hint.setStyleSheet("color:#9AA4AF;")
                self.ws_lay.addWidget(hint)
            return
        gd = self.groups[gp]
        self.ws_title.setText(gd.rel)
        self.ws_title.setToolTip(str(gd.path))
        self.btn_group_excl.setEnabled(True)
        self._refresh_ws_header()
        for pair in gd.pairs:
            self.ws_lay.addWidget(PairCard(gd, pair, self.manager,
                                            lambda p=gp: self._on_pair_change(p),
                                            on_zoom=lambda pr, g0=gd: self._open_zoom_at(str(g0.path), pr)))
        if not gd.pairs:
            hint = QLabel("该组没有图片。")
            hint.setStyleSheet("color:#9AA4AF;")
            self.ws_lay.addWidget(hint)
        self.manager.schedule_reprioritize()

    def _refresh_ws_header(self):
        gd = self.groups.get(self.current_group)
        if gd is None:
            return
        pairs_n = only_r = only_d = 0
        for pr in gd.pairs:
            r = pr.rgb is not None and pr.rgb not in gd.exc_rgb and not gd.exc_whole
            d = pr.depth is not None and pr.depth not in gd.exc_depth and not gd.exc_whole
            if r and d:
                pairs_n += 1
            elif r:
                only_r += 1
            elif d:
                only_d += 1
        self.ws_stats.setText(
            f"图片对 {pairs_n} · 仅RGB {only_r} · 仅深度 {only_d}　（全组：RGB {len(gd.rgb)} · 深度 {len(gd.depth)}）")
        dead = not gd.remaining()
        self.btn_group_excl.setText("恢复整组" if dead else "排除整组")
        self.btn_group_excl.setObjectName("chipRestore" if dead else "chipExclude")
        cu.restyle(self.btn_group_excl)
        self.banner.setVisible(dead)

    def _on_pair_change(self, gp):
        self._sync_group(gp)

    def _toggle_group_excluded(self):
        gp = self.current_group
        if not gp or gp not in self.groups:
            return
        gd = self.groups[gp]
        self._set_group_excluded(gp, gd.remaining())

    def _set_group_excluded(self, gp, excl: bool):
        gd = self.groups[gp]
        if excl:
            gd.exc_whole = True
        else:
            gd.restore_all()
        cu.flash(self.ws_content)
        self._sync_group(gp)
        if not excl and gd.remaining():
            self._select_group_item(gp)   # 恢复整组后跳转到该组（与排除后的自动切换对称）

    def _toggle_image_excluded(self, img_path: str, depth_mode: bool):
        gp = self.img_owner.get(img_path)
        if gp is None:
            return
        gd = self.groups[gp]
        p = Path(img_path)
        exc = gd.exc_depth if depth_mode else gd.exc_rgb
        if gd.exc_whole or p in exc:
            gd.clear_whole_keep_excl()
            exc.discard(p)
        else:
            exc.add(p)
        self._sync_group(gp)

    def _update_nav_stats(self):
        tr = td = gn = 0
        blocks = set()
        for gd in self.groups.values():
            if not gd.remaining():
                continue
            gn += 1
            blocks.add(gd.block)
            tr += len(gd.rgb_retained())
            td += len(gd.depth_retained())
        self.status_lbl.setText(
            f"数据集：{self.root or '—'}　｜　保留RGB {tr} 张 · 保留深度 {td} 张 ｜ "
            f"剩余组 {gn} ｜ 块 {len(blocks)}　｜　组总数 {len(self.groups)}")

    # ------------------------------------------------ 快捷键
    def _hk_zoom(self):
        if not self.groups:
            self._notify("请先加载数据")
            return
        cur = self.groups.get(self.current_group)
        block = cur.block if cur else None
        seq_gd = None
        if block:
            seq_gd = next((gd for gd in self.groups.values()
                           if gd.block == block and gd.remaining()), None)
        if seq_gd is None:
            seq_gd = next((gd for gd in self.groups.values() if gd.remaining()), None)
        if seq_gd is None:
            self._notify("没有保留的组可查看")
            return
        if not any(p.rgb is not None or p.depth is not None for p in seq_gd.pairs):
            self._notify("该组没有图片")
            return
        self._open_zoom_at(str(seq_gd.path))

    def _open_zoom_at(self, gp, pair=None):
        """打开图片对放大浏览（与空格同一界面）；pair 指定起始图片对。"""
        gd = self.groups.get(gp)
        if gd is None:
            return
        pairs = [p for p in gd.pairs if p.rgb is not None or p.depth is not None]
        if not pairs:
            self._notify("该组没有图片", 2000)
            return
        idx = 0
        if pair is not None:
            idx = next((i for i, pr in enumerate(pairs) if pr is pair), 0)
        ZoomWalkDialog(self, gd, lambda: self._sync_group(gp), start_index=idx).exec()

    # ------------------------------------------------ 导出 / 拷贝
    def collect_state(self) -> dict:
        pairs, only_rgb, only_depth = [], [], []
        blocks = set()
        groups_n = 0
        for gd in self.groups.values():
            if not gd.remaining():
                continue
            groups_n += 1
            blocks.add(gd.block)
            for pr in gd.pairs:
                r = pr.rgb is not None and pr.rgb not in gd.exc_rgb
                d = pr.depth is not None and pr.depth not in gd.exc_depth
                if r and d:
                    pairs.append(str(pr.rgb))
                elif r:
                    only_rgb.append(str(pr.rgb))
                elif d:
                    only_depth.append(str(pr.depth))
        return {
            "图片对": pairs,
            "仅RGB图": only_rgb,
            "仅深度图": only_depth,
            # 额外记录会话内全部组（含完全排除的组），供本工具导入时完整复原
            "全部组": [str(gd.path) for gd in self.groups.values()],
            "统计信息": {
                "数据集根目录绝对路径": str(self.root) if self.root else "",
                "最终保留的总块数": len(blocks),
                "最终保留的总组数": groups_n,
                "最终保留的总RGB图数": len(pairs) + len(only_rgb),
                "最终保留的总深度图数": len(pairs) + len(only_depth),
                "仅RGB图总数": len(only_rgb),
                "仅深度图总数": len(only_depth),
            },
        }

    def export_to(self, file_path) -> bool:
        try:
            Path(file_path).write_text(
                json.dumps(self.collect_state(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"写入文件失败：{e}")
            return False
        return True

    def _export_dialog(self):
        if not self.groups:
            QMessageBox.warning(self, "提示", "请先加载数据。")
            return
        name = self.root.name if self.root else "数据集"
        default = FILTERING_DIR / f"精筛_{name}.json"
        f, _ = QFileDialog.getSaveFileName(self, "导出精筛状态（可通过「导入」完整复原）", str(default), "JSON (*.json)")
        if not f:
            return
        if self.export_to(f):
            QMessageBox.information(self, "导出成功", f"精筛状态已导出到：\n{f}")

    def _import_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "导入筛选状态（工具1或工具2导出的JSON均可）",
                                           str(FILTERING_DIR), "JSON (*.json)")
        if f:
            self.import_json(f)

    def _copy_dialog(self):
        files = collect_copy_files(self.groups)
        if not files:
            QMessageBox.warning(self, "提示", "当前没有保留的图片可拷贝。")
            return
        dest = QFileDialog.getExistingDirectory(
            self, f"选择拷贝目标目录（将按原路径结构拷贝 {len(files)} 张图片）", str(COPY_DATA_DIR))
        if not dest:
            return
        CopyDialog(self, files, Path(dest))


def main():
    # 复用已有 QApplication（run.py 启动器场景）；宿主窗口内与工具1同窗切换
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(TOOL_QSS)
    shell = cu.ToolShell("tool2")
    shell.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
