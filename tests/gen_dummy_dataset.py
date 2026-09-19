"""生成用于测试的模拟数据集（含成对/不成对 RGB/深度图、16bit 深度 PNG、空组等情况）。"""
from __future__ import annotations

import random
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent / "filtering" / "test_data"

# 块 -> {二级目录: {场景: (RGB数量, 深度数量)}}
SPEC = {
    "碳钢件-合格品": {
        "M8螺栓-正常": {"001": (5, 5), "002": (5, 4), "003": (3, 5)},
        "M8螺栓-电镀": {"001": (4, 4), "002": (2, 0)},
    },
    "碳钢件-表面划伤": {
        "M8螺栓-划伤": {"001": (6, 6), "002": (4, 3), "003": (0, 3)},
    },
    "铝合金件-阳极氧化": {
        "法兰盘-氧化": {"001": (3, 3), "002": (0, 0)},
        "垫片-氧化": {"001": (7, 7)},
    },
}


def make_color(path: Path, idx: int):
    w, h = 96, 64
    img = Image.new("RGB", (w, h))
    px = img.load()
    base = random.Random(idx)
    r0, g0, b0 = base.randint(30, 220), base.randint(30, 220), base.randint(30, 220)
    for y in range(h):
        for x in range(w):
            n = ((x * 7 + y * 13 + idx * 31) % 37) - 18
            px[x, y] = (max(0, min(255, r0 + n)), max(0, min(255, g0 + n // 2)), max(0, min(255, b0 + n)))
    img.save(path, quality=88)


def make_depth(path: Path, idx: int):
    w, h = 96, 64
    img = Image.new("I;16", (w, h))
    img.putdata([400 + ((x * 3 + y * 5 + idx * 17) % 2600) for y in range(h) for x in range(w)])
    img.save(path)


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    random.seed(7)
    n_rgb = n_depth = n_group = 0
    for block, level2s in SPEC.items():
        for l2, scenes in level2s.items():
            for scene, (c_rgb, c_depth) in scenes.items():
                gdir = ROOT / block / l2 / scene
                (gdir / "color").mkdir(parents=True)
                (gdir / "depth").mkdir(parents=True)
                (gdir / "camera_params").mkdir()
                (gdir / "camera_params" / "intri_param.json").write_text("{}", encoding="utf-8")
                (gdir / "coco").mkdir()
                for i in range(c_rgb):
                    make_color(gdir / "color" / f"{i:04d}.jpg", i + n_group * 100)
                    n_rgb += 1
                for i in range(c_depth):
                    make_depth(gdir / "depth" / f"{i:04d}.png", i + n_group * 100)
                    n_depth += 1
                n_group += 1
    print(f"生成数据集: {ROOT}")
    print(f"块 {len(SPEC)} 组 {n_group} RGB {n_rgb} 深度 {n_depth}")


if __name__ == "__main__":
    sys.exit(main())
