"""两个数据集筛选工具共用的基础设施。

包含：工具宿主窗口（同窗双工具切换）、数据集扫描、图片异步缩略图加载（可见优先 + LRU 缓存）、
流式缩略图条、可缩放图片面板（单图 / 图片对共享缩放）、剪贴板与系统工具函数。

数据集结构约定（“块” = 一级目录，“组” = 场景目录）：

    数据集根目录/一级目录(工件描述)/二级目录(工件名称+状态)/场景目录(001…)/
        color/          若干RGB图
        depth/          若干深度图
        camera_params/
        coco/
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import traceback
import weakref
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QRect, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QFrame, QHBoxLayout, QLabel, QLayout, QMainWindow, QMenu,
                               QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


class ToolShell(QMainWindow):
    """工具宿主窗口：同一窗口内承载两个工具页面，切换时不关窗、不换窗口。

    - 页面（各工具的 MainWindow，QWidget 基类）按需懒创建并常驻；
    - 切换时把来源页面的 _pending_handoff（数据集/选中区）交给目标页面，
      由页面决定加载新数据集还是保留工作状态。
    """

    _TITLES = {"tool1": "数据集筛选 · 工具1（组级初筛）",
               "tool2": "数据集精筛 · 工具2（RGB/深度对比删减）"}

    def __init__(self, initial="tool1", parent=None):
        super().__init__(parent)
        self.setWindowTitle("数据集筛选工具集")
        self.resize(1600, 920)
        self._pages = {}
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        # 两个工具的 QSS 按对象名区分，合并后互不影响
        app = QApplication.instance()
        if app is not None:
            extra = []
            try:
                import tool2_refine
                extra.append(tool2_refine.TOOL_QSS)
            except Exception:
                pass
            try:
                import tool1_filter
                extra.append(tool1_filter.TOOL_QSS)
            except Exception:
                pass
            for css in extra:
                if css and css not in (app.styleSheet() or ""):
                    app.setStyleSheet((app.styleSheet() or "") + css)
        self.show_tool(initial)

    def _page(self, name):
        if name not in self._pages:
            if name == "tool1":
                import tool1_filter as mod
            else:
                import tool2_refine as mod
            page = mod.MainWindow()
            page.switchRequested.connect(self.show_tool)
            page.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._pages[name] = page
            self.stack.addWidget(page)
        return self._pages[name]

    def show_tool(self, name):
        if name not in self._TITLES:
            return
        src = self.stack.currentWidget()
        handoff = getattr(src, "_pending_handoff", None) if src is not None else None
        page = self._page(name)
        if isinstance(handoff, dict):
            try:
                # 数据集相同则保留工作状态，不同则清场加载新数据集
                page.apply_handoff(handoff.get("root"), handoff.get("selected"))
            except Exception:
                traceback.print_exc()
        self.stack.setCurrentWidget(page)
        self.setWindowTitle(self._TITLES[name])
        page.setFocus(Qt.FocusReason.OtherFocusReason)

# ---------------------------------------------------------------- 数据模型

_NUM_RE = re.compile(r"(\d+)")


def natural_key(text):
    """自然排序键：`a2` < `a10`。"""
    return tuple((0, int(tok)) if tok.isdigit() else (1, tok.lower())
                 for tok in _NUM_RE.split(str(text)) if tok != "")


@dataclass
class Group:
    """一个“组”（场景目录）。"""
    path: Path      # 组目录绝对路径
    rel: str        # 相对数据集根目录的路径（posix 风格）
    block: str      # 所属“块”（一级目录名）
    rgb: list       # color/ 下的图片绝对路径（自然排序）
    depth: list     # depth/ 下的图片绝对路径（自然排序）


_COLOR_NAMES = {"color"}
_DEPTH_NAMES = {"depth"}


def _child_dir(folder, names: set):
    """在 folder 下查找名称匹配（大小写不敏感）的子目录，返回其 Path 或 None。"""
    try:
        for e in os.scandir(folder):
            if e.is_dir() and not e.name.startswith(".") and e.name.lower() in names:
                return Path(e.path)
    except OSError:
        pass
    return None


def list_images(folder) -> list:
    """列出目录下的图片文件（自然排序）。

    兼容回退：若目录下没有直接图片文件但存在子目录，则下探一层收集图片
    （应对 color/内再分一级子目录 的数据组织方式）。
    """
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    files = [n for n in names if not n.startswith(".") and Path(n).suffix.lower() in IMAGE_EXTS]
    files.sort(key=natural_key)
    out = [Path(folder) / n for n in files]
    if not out:
        subs = sorted((n for n in names if not n.startswith(".")
                       and (Path(folder) / n).is_dir()), key=natural_key)
        for sub in subs:
            try:
                sub_names = os.listdir(Path(folder) / sub)
            except OSError:
                continue
            sub_files = [n for n in sub_names
                         if not n.startswith(".") and Path(n).suffix.lower() in IMAGE_EXTS]
            sub_files.sort(key=natural_key)
            out += [Path(folder) / sub / n for n in sub_files]
    return out


def make_group(scene_dir, root) -> Group:
    scene_dir = Path(scene_dir).resolve()
    rel = scene_dir.relative_to(root).as_posix()
    color_dir = _child_dir(scene_dir, _COLOR_NAMES)
    depth_dir = _child_dir(scene_dir, _DEPTH_NAMES)
    return Group(scene_dir, rel, rel.split("/", 1)[0],
                 list_images(color_dir) if color_dir else [],
                 list_images(depth_dir) if depth_dir else [])


def scan_dataset(root) -> list:
    """扫描数据集：返回按 块/相对路径 自然排序的 Group 列表。

    判定规则：某目录下存在 color/ 或 depth/ 子目录（大小写不敏感）即视为“组”，
    不再向其内部递归；“块” = 组的一级上级目录。
    """
    root = Path(root).resolve()
    groups = []

    def is_scene(d: Path) -> bool:
        return (_child_dir(d, _COLOR_NAMES) is not None
                or _child_dir(d, _DEPTH_NAMES) is not None)

    def walk(d: Path, depth: int):
        try:
            entries = [e for e in os.scandir(d) if e.is_dir() and not e.name.startswith(".")]
        except OSError:
            return
        entries.sort(key=lambda e: natural_key(e.name))
        for e in entries:
            p = Path(e.path)
            if is_scene(p):
                groups.append(make_group(p, root))
            elif depth < 5:   # 兼容更深的数据集层级（标准结构为 3 层）
                walk(p, depth + 1)

    walk(root, 0)
    return groups


# ---------------------------------------------------------------- 图片加载

def _normalize_depth(pil: Image.Image) -> Image.Image:
    """深度图归一化：任意位深 → 8bit 灰度（最小-最大拉伸）。

    注意：PIL 对 I/F 模式的 point() 会探测 lambda 是否为仿射函数，
    因此这里必须写纯算术表达式，不能调用 float()/int()/min() 等。
    """
    if pil.mode in ("I", "F") or pil.mode.startswith("I;16"):
        if pil.mode != "I":
            pil = pil.convert("I")
        mn, mx = pil.getextrema()
        if mx > mn:
            k = 255.0 / (float(mx) - float(mn))
            pil = pil.point(lambda v: (v - mn) * k)
        else:
            pil = pil.point(lambda v: 127)
        return pil.convert("L")
    if pil.mode != "L":
        pil = pil.convert("L")
    return pil


def _pil_to_qimage(pil: Image.Image) -> QImage:
    if pil.mode != "RGBA":
        pil = pil.convert("RGBA")
    data = pil.tobytes("raw", "RGBA")
    img = QImage(data, pil.width, pil.height, QImage.Format.Format_RGBA8888)
    return img.copy()  # 脱离临时缓冲区，保证跨线程安全


def _win_long_path(path) -> str:
    r"""Windows 超长路径（>=240字符）加 \\?\ 前缀，保证 PIL 的 C 层 fopen 可打开。"""
    s = str(path)
    if os.name == "nt" and len(s) >= 240 and not s.startswith("\\\\?\\"):
        s = "\\\\?\\" + os.path.abspath(s)
    return s


def load_qimage(path, max_size: int, depth_mode: bool = False):
    """加载图片并缩放到 max_size 内（线程安全）。深度图做 8bit 归一化。失败返回 None。"""
    try:
        with Image.open(_win_long_path(path)) as im:
            if not depth_mode:
                try:
                    im.draft(None, (int(max_size), int(max_size)))  # JPEG 解码期降采样，大幅提速
                except Exception:
                    pass
            im.load()
            if depth_mode:
                im = _normalize_depth(im)
            im = ImageOps.exif_transpose(im)
            im.thumbnail((int(max_size), int(max_size)), Image.Resampling.BILINEAR)
            img = _pil_to_qimage(im)
            if img is None or img.isNull():
                raise RuntimeError("生成的 QImage 为空")
            return img
    except Exception:
        traceback.print_exc()
        return None


# ---------------------------------------------------------------- 异步缩略图

_VISIBLE_PRI = 0      # 可见项优先
_DEFAULT_PRI = 9      # 普通后台项


class _LoadJob(QRunnable):
    def __init__(self, mgr: "ThumbManager", key: tuple):
        super().__init__()
        self._mgr = mgr
        self._key = key

    def run(self):
        path, size, depth = self._key
        img = load_qimage(path, size * 2, depth)  # 2 倍采样，保证缩小后清晰
        self._mgr.ready.emit(self._key, img)


class ThumbManager(QObject):
    """缩略图异步加载器：LRU 缓存 + 可见优先调度。所有接口须在主线程调用。"""

    ready = Signal(object, object)
    progress = Signal(int, int)   # （已就绪数, 总请求数）—— 用于状态栏加载进度

    def __init__(self, parent=None, cache_limit=600):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(4, min(12, os.cpu_count() or 4)))
        self._cache = OrderedDict()      # key -> QPixmap
        self._cache_limit = cache_limit
        self._pending = {}               # key -> 优先级（小者优先）
        self._heap = []
        self._seq = 0
        self._inflight = set()
        self._waiters = defaultdict(list)
        self._strips = weakref.WeakSet()
        self._placeholder = {}
        self._failure = {}
        self._repri_pending = False      # 重排序请求合并标志
        self._n_req = 0
        self._n_done = 0
        self.failed_count = 0            # 解码失败的图片数（用于状态栏提示）
        self.ready.connect(self._on_ready)

    @staticmethod
    def make_key(path, size: int, depth: bool) -> tuple:
        return (str(path), int(size), bool(depth))

    def _decor(self, size: int, text: str, bg: str, fg: str) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(QColor(bg))
        p = QPainter(pm)
        p.setPen(QColor(fg))
        f = p.font()
        f.setPointSize(max(8, size // 8))
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, text)
        p.end()
        return pm

    def placeholder(self, size: int) -> QPixmap:
        if size not in self._placeholder:
            self._placeholder[size] = self._decor(size, "…", "#21262D", "#6E7681")
        return self._placeholder[size]

    def failure(self, size: int) -> QPixmap:
        if size not in self._failure:
            self._failure[size] = self._decor(size, "×", "#3D1D20", "#F85149")
        return self._failure[size]

    def get(self, key: tuple, cb, priority: int = _DEFAULT_PRI):
        """请求缩略图；就绪后在主线程回调 cb(QPixmap)。"""
        pm = self._cache.get(key)
        if pm is not None:
            self._cache.move_to_end(key)
            cb(pm)
            self._dispatch()
            return
        first = key not in self._pending and key not in self._inflight
        if first:
            self._n_req += 1
        self._waiters[key].append(cb)
        if first:
            self._pending[key] = priority
            self._seq += 1
            heappush(self._heap, (priority, self._seq, key))
        self._dispatch()

    def set_priority(self, key: tuple, pri: int):
        cur = self._pending.get(key)
        if cur is not None and pri < cur:
            self._pending[key] = pri
            self._seq += 1
            heappush(self._heap, (pri, self._seq, key))
            self._dispatch()

    def _dispatch(self):
        # 按优先级出队调度，可见项优先；所有请求最终都会加载
        limit = self._pool.maxThreadCount()
        while len(self._inflight) < limit and self._heap:
            pri, _seq, key = self._heap[0]
            cur = self._pending.get(key)
            if cur is None or cur != pri:   # 过期堆项
                heappop(self._heap)
                continue
            heappop(self._heap)
            del self._pending[key]
            self._inflight.add(key)
            self._pool.start(_LoadJob(self, key))

    @Slot(object, object)
    def _on_ready(self, key, qimg):
        self._inflight.discard(key)
        if qimg is not None and not qimg.isNull():
            pm = QPixmap.fromImage(qimg)
            if not pm.isNull():
                self._cache[key] = pm
                if len(self._cache) > self._cache_limit:
                    self._cache.popitem(last=False)
            else:
                pm = self.failure(key[1])
                self.failed_count += 1
        else:
            pm = self.failure(key[1])
            self.failed_count += 1
        self._n_done += 1
        self.progress.emit(self._n_done, self._n_req)
        for cb in self._waiters.pop(key, ()):
            try:
                cb(pm)
            except Exception:
                traceback.print_exc()
        self._dispatch()

    def register_strip(self, strip: "ThumbStrip"):
        self._strips.add(strip)

    def schedule_reprioritize(self):
        """请求重新按可见性排序待加载项；多次请求会被合并为一次。"""
        if self._repri_pending:
            return
        self._repri_pending = True
        QTimer.singleShot(120, self._reprioritize_all)

    def _reprioritize_all(self):
        self._repri_pending = False
        for strip in list(self._strips):
            try:
                strip.prioritize_visible()  # 内部自带 isVisible + 几何可见性判断
            except RuntimeError:
                pass

    def wait(self, ms=5000):
        self._pool.waitForDone(ms)


# ---------------------------------------------------------------- 布局与通用控件

class FlowLayout(QLayout):
    """流式布局：子项按行排布、自动换行。"""

    def __init__(self, parent=None, margin=4, hspacing=6, vspacing=6):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._hspacing = hspacing
        self._vspacing = vspacing
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do_layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y = eff.x(), eff.y()
        line_h = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._hspacing
            if next_x - self._hspacing > eff.right() + 1 and line_h > 0:
                x = eff.x()
                y = y + line_h + self._vspacing
                line_h = 0
                next_x = x + hint.width() + self._hspacing
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h + m.bottom() - rect.y()


def _make_pixmap_cb(lbl: QLabel, size: int, fit: bool = False):
    """缩略图交付回调：等比缩放到 size 内。

    fit=True 时标签同时收缩为图片实际尺寸——紧凑排布，消除方形标签内的上下空白。
    """
    ref = weakref.ref(lbl)

    def cb(pm: QPixmap):
        target = ref()
        if target is None:
            return
        try:
            pm2 = pm.scaled(size, size,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
            if fit:
                target.setFixedSize(pm2.size())
            target.setPixmap(pm2)
            target.setProperty("loaded", True)
        except RuntimeError:
            pass

    return cb


def _geom_visible(w: QWidget, margin: int = 0) -> bool:
    """几何可见性：控件矩形映射到最近滚动区视口后是否与其相交。

    只依赖布局几何，不依赖窗口绘制状态。
    """
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QAbstractScrollArea):
            vp = p.viewport()
            top_left = w.mapTo(vp, QPoint(0, 0))
            rect = QRect(top_left, w.size())
            if margin:
                rect = rect.adjusted(-margin, -margin, margin, margin)
            return rect.intersects(vp.rect())
        p = p.parentWidget()
    return True  # 不在滚动区内，视为可见


class ThumbStrip(QWidget):
    """一组缩略图（流式布局）。左键查看大图；右键：查看 / 复制图片 / 复制路径 / 打开所在文件夹。"""

    viewRequested = Signal(str, bool)   # (图片绝对路径, 是否深度图)

    def __init__(self, manager: ThumbManager, entries, thumb: int = 132, parent=None,
                 fit_height: bool = False, spacing: int = 6):
        """entries: iterable of (path, depth_mode)。

        fit_height=True：图片加载后标签收缩为其实际尺寸（紧凑排布，适合多图条）；
        False：保持正方形占位（适合需要与固定占位框对齐的单图场景）。
        """
        super().__init__(parent)
        self._manager = manager
        self._thumb = thumb
        self._entries = []
        lay = FlowLayout(self, margin=0, hspacing=spacing, vspacing=spacing)
        self.setLayout(lay)
        for path, depth in entries:
            lbl = QLabel(self)
            lbl.setFixedSize(thumb, thumb)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setPixmap(manager.placeholder(thumb))
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.setToolTip(f"{path}\n左键：查看大图；右键：复制等操作")
            lay.addWidget(lbl)
            key = manager.make_key(path, thumb, depth)
            entry = {"lbl": lbl, "path": str(path), "depth": bool(depth), "key": key}
            self._entries.append(entry)
            lbl.installEventFilter(self)
            manager.get(key, _make_pixmap_cb(lbl, thumb, fit_height))
        manager.register_strip(self)

    def prioritize_visible(self):
        """把几何可见（含滚动缓冲范围）的未加载缩略图提升为优先加载。"""
        if not self.isVisible():
            return
        if not _geom_visible(self, margin=600):
            return
        mgr = self._manager
        for e in self._entries:
            lbl = e["lbl"]
            try:
                if lbl.property("loaded"):
                    continue
                mgr.set_priority(e["key"], _VISIBLE_PRI)
            except RuntimeError:
                continue

    def showEvent(self, ev):
        super().showEvent(ev)
        self._manager.schedule_reprioritize()

    def eventFilter(self, obj, ev):
        entry = next((e for e in self._entries if e["lbl"] is obj), None)
        if entry is not None:
            if ev.type() == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton:
                self.viewRequested.emit(entry["path"], entry["depth"])
                return True
            if ev.type() == QEvent.Type.ContextMenu:
                self._context_menu(entry, ev.globalPos())
                return True
        return super().eventFilter(obj, ev)

    def _context_menu(self, entry, gpos):
        menu = QMenu(self)
        a_view = menu.addAction("查看大图")
        a_copy = menu.addAction("复制图片")
        a_path = menu.addAction("复制文件路径")
        a_dir = menu.addAction("打开所在文件夹")
        act = menu.exec(gpos)
        if act == a_view:
            self.viewRequested.emit(entry["path"], entry["depth"])
        elif act == a_copy:
            copy_image_to_clipboard(entry["path"])
        elif act == a_path:
            QGuiApplication.clipboard().setText(entry["path"])
        elif act == a_dir:
            reveal_in_file_manager(entry["path"])


class ElidedLabel(QLabel):
    """过长文本自动中段省略的 QLabel。"""

    def __init__(self, text="", parent=None):
        self._full = ""
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(30)
        self._full = text or ""
        super().setText(self._elided())

    def full_text(self):
        return self._full

    def setText(self, text):
        self._full = text or ""
        super().setText(self._elided())

    def _elided(self):
        fm = self.fontMetrics()
        return fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(10, self.width() - 4))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        super().setText(self._elided())


def restyle(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def flash(widget, msec=800):
    """短暂高亮一个控件（配合 QSS `[flash="true"]` 规则）。"""
    try:
        widget.setProperty("flash", True)
        restyle(widget)
    except RuntimeError:
        return

    def _clear():
        try:
            widget.setProperty("flash", False)
            restyle(widget)
        except RuntimeError:
            pass

    QTimer.singleShot(msec, _clear)


# ---------------------------------------------------------------- 图片查看

class FitImagePane(QScrollArea):
    """单图滚动面板：初始适应窗口（小图放大、大图缩小，保证合适大小），
    滚轮缩放（锚定光标），拖拽平移，双击复位。"""

    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm = None
        self._scale = 1.0
        self._auto_fit = True
        self._drag = None
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setWidget(self._label)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea{background:#1E2227;border:none;}"
                           "QLabel{color:#8B949E;}")
        self.setMinimumSize(120, 120)

    def set_image(self, qimg):
        self._pm = QPixmap.fromImage(qimg) if qimg is not None else None
        self._auto_fit = True
        if self._pm is None or self._pm.isNull():
            self.clear("（图片加载失败）")
            return
        self._apply_fit()

    def clear(self, msg: str = "（无图片）"):
        """清空并显示占位说明（区别于加载失败）。"""
        self._pm = None
        self._label.setPixmap(QPixmap())
        self._label.setText(msg)
        self._label.resize(max(200, self.viewport().width()), max(60, self.viewport().height() // 3))

    def current_pixmap(self):
        return self._label.pixmap()

    def has_image(self) -> bool:
        return self._pm is not None and not self._pm.isNull()

    def scale(self) -> float:
        return self._scale

    def fit_scale(self):
        """计算“适应窗口”的缩放比例（不应用）；无图返回 None。"""
        if not self.has_image():
            return None
        vw, vh = self.viewport().width() - 12, self.viewport().height() - 12
        return max(0.02, min(vw / self._pm.width(), vh / self._pm.height()))

    def set_scale(self, s: float, refit_marks=True):
        """按指定比例显示（用于外部同步缩放）。"""
        if not self.has_image():
            return
        self._scale = max(0.02, min(12.0, s))
        if refit_marks:
            self._auto_fit = False
        self._apply()

    def _apply(self):
        if self._pm is None:
            return
        if abs(self._scale - 1.0) < 1e-4:
            pm = self._pm
        else:
            pm = self._pm.scaled(QSize(max(1, int(self._pm.width() * self._scale)),
                                       max(1, int(self._pm.height() * self._scale))),
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self._label.setPixmap(pm)
        self._label.resize(pm.size())

    def _apply_fit(self):
        s = self.fit_scale()
        if s is None:
            return
        self._scale = s
        self._apply()

    def wheelEvent(self, ev):
        if self._pm is None:
            return
        self._auto_fit = False
        pos = ev.position().toPoint()
        lw, lh = max(1, self._label.width()), max(1, self._label.height())
        rx = (self.horizontalScrollBar().value() + pos.x()) / lw
        ry = (self.verticalScrollBar().value() + pos.y()) / lh
        factor = 1.25 if ev.angleDelta().y() > 0 else 0.8
        self._scale = min(12.0, max(0.02, self._scale * factor))
        self._apply()
        self.horizontalScrollBar().setValue(int(rx * self._label.width() - pos.x()))
        self.verticalScrollBar().setValue(int(ry * self._label.height() - pos.y()))
        self.zoomChanged.emit(self._scale)

    def mouseDoubleClickEvent(self, ev):
        self._auto_fit = True
        self._apply_fit()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._auto_fit:
            self._apply_fit()

    def mousePressEvent(self, ev):
        if ev.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._drag = (ev.globalPosition().toPoint(),
                          self.horizontalScrollBar().value(), self.verticalScrollBar().value())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, ev):
        if self._drag:
            start, hs, vs = self._drag
            dp = ev.globalPosition().toPoint() - start
            self.horizontalScrollBar().setValue(hs - dp.x())
            self.verticalScrollBar().setValue(vs - dp.y())

    def mouseReleaseEvent(self, ev):
        self._drag = None
        self.setCursor(Qt.CursorShape.ArrowCursor)


class PairSideBox(QFrame):
    """图片对中的一侧：图片面板 + 标题 + （可选）底部按钮行。

    动态属性 excl="true" 时显示红色边框（配合 QSS）。"""

    def __init__(self, caption, parent=None):
        super().__init__(parent)
        self.setObjectName("pairSideBox")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.pane = FitImagePane()
        v.addWidget(self.pane, 1)
        self.caption = QLabel(caption)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setStyleSheet("color:#8B949E;padding:2px;")
        v.addWidget(self.caption)
        self.btn_row = QWidget()
        self.btn_lay = QHBoxLayout(self.btn_row)
        self.btn_lay.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.btn_row)
        self.btn_row.hide()

    def show_buttons(self, *buttons):
        for b in buttons:
            self.btn_lay.addWidget(b)
        self.btn_row.show()

    def set_excluded(self, excl: bool):
        self.setProperty("excl", "true" if excl else "false")
        self.setStyleSheet(
            "QFrame#pairSideBox{border:2px solid #F85149;border-radius:6px;}" if excl else "")


class PairViewWidget(QWidget):
    """RGB / 深度并排显示（共享同一缩放比例，左右对称）。

    - 初始按两图共同适配比例显示（小图放大、大图缩小）；
    - 滚轮缩放任一侧，另一侧同步；双击任一侧，两侧共同复位。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._syncing = False
        self._user_zoomed = False
        self.box_rgb = PairSideBox("RGB")
        self.box_depth = PairSideBox("深度")
        self.split = QSplitter()
        self.split.addWidget(self.box_rgb)
        self.split.addWidget(self.box_depth)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([10000, 10000])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.split)
        for box in (self.box_rgb, self.box_depth):
            box.pane.zoomChanged.connect(lambda s, src=box: self._sync_zoom(src, s))

    def _other(self, box):
        return self.box_depth if box is self.box_rgb else self.box_rgb

    def _sync_zoom(self, src, scale: float):
        if self._syncing:
            return
        self._syncing = True
        try:
            self._other(src).pane.set_scale(scale)
        finally:
            self._syncing = False
        self._user_zoomed = True

    def set_images(self, rgb_img, depth_img, rgb_caption=None, depth_caption=None):
        """设置两侧图片（QImage 或 None）；缺失一侧显示占位说明。"""
        self._user_zoomed = False
        for box, img, caption, name in (
                (self.box_rgb, rgb_img, rgb_caption, "RGB"),
                (self.box_depth, depth_img, depth_caption, "深度")):
            if img is not None:
                box.pane.set_image(img)
            else:
                box.pane.clear()   # 该侧缺图：显示“（无图片）”占位
                box.caption.setText(f"{name} · 无")
                continue
            box.caption.setText(caption if caption else name)
        self.fit_common()

    def fit_common(self):
        """按两图共同比例适配（保证左右图片范围对称）。"""
        scales = [b.pane.fit_scale() for b in (self.box_rgb, self.box_depth)]
        scales = [s for s in scales if s is not None]
        if not scales:
            return
        s = min(scales)
        for b in (self.box_rgb, self.box_depth):
            b.pane.set_scale(s, refit_marks=True)
            b.pane._auto_fit = False  # 由本组件统一管理适配

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if not self._user_zoomed:
            self.fit_common()


# ---------------------------------------------------------------- 系统工具

def copy_image_to_clipboard(path) -> bool:
    """复制图片到剪贴板：同时写入图像数据与文件引用（可直接粘贴到资源管理器）。"""
    try:
        cb = QGuiApplication.clipboard()
        mime = QMimeData()
        img = QImage(str(path))
        if not img.isNull():
            mime.setImageData(img)
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        cb.setMimeData(mime)
        return True
    except Exception:
        traceback.print_exc()
        return False


def reveal_in_file_manager(path):
    path = str(path)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        traceback.print_exc()
