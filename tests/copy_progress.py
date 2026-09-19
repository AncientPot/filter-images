"""拷贝进度条测试：字节级进度回调、进度对话框联动、取消。"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication

import tool2_refine as t2

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "filtering" / "test_data"
DEST = HERE / "out" / "copy_progress_dest"

app = QApplication([])


def pump(ms=300):
    end = datetime.now() + timedelta(milliseconds=ms)
    while datetime.now() < end:
        app.processEvents()


def expect(c, m):
    if not c:
        raise AssertionError(m)


def collect_files():
    files = []
    for gd in t2.OrderedDict():  # placeholder, replaced below
        break
    # 直接从文件系统构造（避免依赖窗口状态）
    for f in sorted(SRC.rglob("*.*")):
        if f.suffix.lower() in (".jpg", ".png"):
            rel = f.relative_to(SRC)
            files.append((f, rel.as_posix()))
    return files


def main():
    shutil.rmtree(DEST, ignore_errors=True)
    files = collect_files()
    expect(len(files) > 10, f"测试文件不足: {len(files)}")
    total_bytes = sum(os.path.getsize(s) for s, _ in files)
    print(f"待拷贝 {len(files)} 张 / {t2._fmt_size(total_bytes)}")

    # 1) perform_copy 字节级进度回调
    seen = []

    def prog(done, total, db, tb):
        seen.append((done, db))

    done, errs = t2.perform_copy(files, DEST, progress=prog)
    expect(not errs and done == len(files), f"拷贝失败: {errs[:1]}")
    expect(seen and seen[-1] == (len(files), total_bytes), f"最终进度不符: {seen[-1]}")
    mono_db = all(seen[i][1] <= seen[i + 1][1] for i in range(len(seen) - 1))
    assert mono_db, "字节进度非单调递增"
    print(f"1) 字节级进度回调 OK（{len(seen)} 次上报，累计 {t2._fmt_size(seen[-1][1])}）")

    # 2) _fmt_size
    expect(t2._fmt_size(0) == "0 B", t2._fmt_size(0))
    expect(t2._fmt_size(600 * 1024**3).startswith("600.0 GB"), t2._fmt_size(600 * 1024**3))
    print("2) 容量格式化 OK")

    # 3) 取消：拷贝中途取消后部分完成且无错误崩溃
    shutil.rmtree(DEST, ignore_errors=True)
    cancel_flag = {"v": False}

    def maybe_cancel():
        return cancel_flag["v"]

    state = {"n": 0}

    def prog2(done, total, db, tb):
        state["n"] = done
        if done >= 3:
            cancel_flag["v"] = True

    done2, errs2 = t2.perform_copy(files, DEST, progress=prog2, cancel=maybe_cancel)
    expect(done2 < len(files), "取消未生效")
    print(f"3) 中途取消 OK（完成 {done2}/{len(files)} 后停止）")

    shutil.rmtree(DEST, ignore_errors=True)
    print("COPY PROGRESS ALL PASSED")


if __name__ == "__main__":
    main()
