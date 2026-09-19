"""启动入口：直接启动工具1（组级初筛）。

工具2请在工具1界面点击右上角「→ 工具2」切换（同一窗口，数据集自动接续），
或运行：uv run src/tool2_refine.py
"""
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def main():
    import tool1_filter
    tool1_filter.main()


if __name__ == "__main__":
    main()
