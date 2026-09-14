#!/usr/bin/env python3
"""產生合成的中醫藥袋，補真實樣本缺的那一種版型。

  python evals/prescription_scan/make_synthetic_tcm.py

公開找得到的中醫藥袋照片，要不是沒印藥名（醫改會那張），就是只拍到藥廠標籤
的局部。量測要回答的「一包混多味會不會被拆成 N 筆藥」需要**有印方名、每次
一包**的藥袋，只好自己畫。版型照常見的診所與醫院中醫部藥袋排，機構與姓名
全是虛構的；報告裡這組以 synthetic 分開列，不跟真實照片混算。
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent / "samples"

_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit("找不到可用的中文字型")


# 每一張：(檔名, 行列表)。行是 (字級, 文字)。
BAGS = {
    # 診所最常見的形狀：一包裡混四味，用法寫「每次一包」。
    "tcm_synth_clinic_mixed.png": [
        (46, "安和中醫診所"),
        (26, "全民健康保險特約醫事機構"),
        (30, ""),
        (32, "姓名：王美○　　性別：女　　年齡：72"),
        (32, "調劑日期：115/09/10　　給藥日數：7 日"),
        (30, ""),
        (34, "處方（每包含）"),
        (32, "　加味逍遙散　　　1.3 公克"),
        (32, "　香附　　　　　　0.3 公克"),
        (32, "　丹參　　　　　　0.3 公克"),
        (32, "　酸棗仁湯　　　　1.0 公克"),
        (30, ""),
        (34, "用法：一日三次，每次一包，飯後服用"),
        (32, "總量：21 包"),
        (30, ""),
        (28, "調劑者：林○○ 中醫師　　電話：(02)2700-0000"),
        (26, "請核對姓名，服藥期間如有不適請洽本診所"),
    ],
    # 醫院中醫部：列每日總量，另起一行寫每次一包。
    "tcm_synth_hospital_daily.png": [
        (44, "康寧醫院　中醫部"),
        (30, "中藥藥袋"),
        (30, ""),
        (32, "病人姓名：陳○○　　病歷號：00123456"),
        (32, "看診日期：115/09/08　　天數：14 天"),
        (30, ""),
        (34, "藥名　　　　　　　每日總量"),
        (32, "葛根湯　　　　　　6.0 g"),
        (32, "川芎茶調散　　　　3.0 g"),
        (32, "辛夷清肺湯　　　　3.0 g"),
        (30, ""),
        (34, "用法：每日三次　每次一包　飯後"),
        (32, "本袋共 42 包，以上藥品已混合分包"),
        (30, ""),
        (28, "醫師：張○○　　藥師：李○○"),
    ],
    # 單一方，睡前一次：看非基準方與 HS 的歸類。
    "tcm_synth_clinic_single.png": [
        (46, "德生中醫診所"),
        (30, ""),
        (32, "姓名：黃○○　　日期：115/09/12"),
        (30, ""),
        (34, "藥名：天王補心丹（濃縮顆粒）"),
        (32, "每次 4 公克，睡前服用"),
        (32, "共 7 包（7 日）"),
        (30, ""),
        (32, "適應症：虛煩少寐、心悸"),
        (28, "調劑者：吳○○ 中醫師"),
    ],
}


def render(lines: list[tuple[int, str]], seed: int) -> Image.Image:
    rng = random.Random(seed)
    width, margin = 1100, 70
    height = margin * 2 + sum(int(size * 1.6) for size, _ in lines)
    # 藥袋是略帶米色的紙，不是純白；純白背景會讓模型把它當成截圖而不是實拍。
    img = Image.new("RGB", (width, height), (246, 243, 234))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, width - 20, height - 20], outline=(40, 120, 70), width=4)
    y = margin
    for size, text in lines:
        if text:
            draw.text((margin, y), text, font=_font(size), fill=(25, 25, 25))
        y += int(size * 1.6)
    # 輕微旋轉與模糊，模擬手機拍攝；幅度刻意小，這組要量的是版型不是畫質。
    img = img.rotate(rng.uniform(-2.5, 2.5), expand=True, fillcolor=(90, 80, 70))
    return img.filter(ImageFilter.GaussianBlur(0.6))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (name, lines) in enumerate(BAGS.items()):
        render(lines, seed=i).save(OUT / name)
        print(f"寫出 {OUT / name}")


if __name__ == "__main__":
    main()
