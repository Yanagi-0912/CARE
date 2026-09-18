#!/usr/bin/env python3
"""把 cases.json 畫成可以直接傳進 LINE 的藥袋圖。

  cd CARE && python evals/interaction_alerts/make_bags.py

每一組測資產出數張圖，檔名前綴標好順序與角色：

  T9_bleeding_nsaid_x_warfarin_1_existing_可邁丁錠．．．.png
  T9_bleeding_nsaid_x_warfarin_2_added_達利炎錠.png

**existing 要先掃、added 後掃**：偵測的觸發側是「這一次新加入的藥」，順序反了
就變成另一組情境（處方藥先加不會觸發，反過來則會）。

版型照台灣藥袋的常見排法，機構、姓名、日期全是虛構的。中藥袋只印方名與用法
——中藥沒有藥證字號，辨識走的是方名比對；西藥袋印中文品名與許可證字號，
那是掃描定出證號、進而取得成分與 ATC 的依據。
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "samples"

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


# 虛構的機構與病人，讓每張圖看起來不是同一天同一個人領的。
CLINICS = ["安和中醫診所", "德生中醫診所", "同仁堂中醫診所", "康寧醫院 中醫部"]
HOSPITALS = ["康寧醫院", "信義綜合醫院", "民生總醫院", "文山醫院"]
PHARMACIES = ["丁丁藥局 復興店", "杏一藥局 民生店", "啄木鳥藥局 文山店"]
PATIENTS = ["王美○", "陳秀○", "林阿○", "黃金○", "張桂○"]


def render(lines: list[tuple[int, str]], seed: int, tint: tuple[int, int, int]) -> Image.Image:
    rng = random.Random(seed)
    width, margin = 1100, 70
    height = margin * 2 + sum(int(size * 1.6) for size, _ in lines)
    # 藥袋是略帶米色的紙，不是純白；純白背景會讓模型把它當截圖而不是實拍。
    img = Image.new("RGB", (width, height), (246, 243, 234))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, width - 20, height - 20], outline=tint, width=4)
    y = margin
    for size, text in lines:
        if text:
            draw.text((margin, y), text, font=_font(size), fill=(25, 25, 25))
        y += int(size * 1.6)
    # 輕微旋轉與模糊，模擬手機拍攝；幅度刻意小，這組要測的是判定不是畫質。
    img = img.rotate(rng.uniform(-2.2, 2.2), expand=True, fillcolor=(90, 80, 70))
    return img.filter(ImageFilter.GaussianBlur(0.6))


def tcm_bag(name: str, rng: random.Random) -> list[tuple[int, str]]:
    return [
        (46, rng.choice(CLINICS)),
        (26, "全民健康保險特約醫事機構"),
        (30, ""),
        (32, f"姓名：{rng.choice(PATIENTS)}　　性別：女　　年齡：{rng.randint(66, 84)}"),
        (32, f"調劑日期：115/09/{rng.randint(10, 18):02d}　　給藥日數：7 日"),
        (30, ""),
        (34, f"藥名：{name}（濃縮顆粒）"),
        (32, f"每次 {rng.choice([2, 3, 4])} 公克，一日三次，飯後服用"),
        (32, "共 21 包"),
        (30, ""),
        (32, "以上藥品已混合分包，每次一包"),
        (28, "調劑者：林○○ 中醫師　　電話：(02)2700-0000"),
        (26, "請核對姓名，服藥期間如有不適請洽本診所"),
    ]


def _usage(dosage_form: str, rng: random.Random) -> str:
    """用法照劑型寫。點眼液寫「一粒」會讓這張圖一看就是假的。"""
    if "眼" in dosage_form:
        return "用法用量：每日三至四次，每次一至二滴，點於患眼"
    if "液" in dosage_form or "糖漿" in dosage_form:
        return "用法用量：每日三次，每次 10 毫升，飯後服用"
    if "散" in dosage_form or "顆粒" in dosage_form:
        return "用法用量：每日三次，每次一包，飯後服用"
    if "膠囊" in dosage_form:
        return "用法用量：每日三次，每次一粒，飯後服用"
    return "用法用量：每日一次，每次一錠，飯後服用"


def rx_bag(name: str, license_number: str, dosage_form: str, rng: random.Random) -> list[tuple[int, str]]:
    return [
        (44, rng.choice(HOSPITALS)),
        (28, "門診藥袋　　交付者：藥師 李○○"),
        (30, ""),
        (32, f"姓名：{rng.choice(PATIENTS)}　　病歷號：00{rng.randint(100000, 999999)}"),
        (32, f"看診日期：115/09/{rng.randint(1, 9):02d}　　給藥日數：28 日"),
        (30, ""),
        (36, f"藥品名稱：{name}"),
        (30, f"衛署許可證字號：{license_number}"),
        (32, _usage(dosage_form, rng)),
        (32, f"總量：28 　　　　　科別：{rng.choice(['心臟內科', '神經內科', '家庭醫學科'])}"),
        (30, ""),
        (28, "※ 服用期間如需自行購買成藥，請先詢問藥師"),
    ]


def otc_box(name: str, license_number: str, dosage_form: str, rng: random.Random) -> list[tuple[int, str]]:
    return [
        (30, rng.choice(PHARMACIES)),
        (48, name),
        (30, ""),
        (32, f"衛署許可證字號：{license_number}"),
        (32, "指示藥品　　　　　　　　　　　　　　適用：成人"),
        (30, ""),
        (32, _usage(dosage_form, rng)),
        (32, f"有效期限：{rng.randint(2027, 2029)}/0{rng.randint(1, 9)}"),
        (30, ""),
        (28, "※ 症狀未改善請洽醫師或藥師，請詳閱說明書"),
    ]


def slug(text: str) -> str:
    return re.sub(r'[^\w一-鿿]+', "", text)[:16]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = json.loads((HERE / "cases.json").read_text("utf-8"))["cases"]
    # 分級與劑型取自藥證庫本身，測資裡不重複記一份——記兩份就會有一天不一致。
    catalog = {
        e["license_number"]: e
        for e in json.loads(
            (HERE.parents[1] / "resources" / "drug_catalog.json").read_text("utf-8")
        )
    }
    written = 0
    for case in cases:
        rng = random.Random(case["id"])
        order = [("existing", s) for s in case["existing"]] + [
            ("added", s) for s in case["added"]
        ]
        for i, (slot, spec) in enumerate(order, start=1):
            if spec.get("kind") == "tcm":
                lines, tint = tcm_bag(spec["name"], rng), (40, 120, 70)
            else:
                # 版型照藥事法分級，不照它在這組測資裡的角色：處方藥永遠是
                # 醫院門診藥袋，成藥永遠是藥局買回來的外盒。用 slot 決定會畫出
                # 「處方藥印成指示藥品外盒」這種一看就假的圖。
                entry = catalog.get(spec["license_number"], {})
                form = entry.get("dosage_form", "")
                if entry.get("drug_class") == "prescription":
                    lines, tint = rx_bag(spec["name"], spec["license_number"], form, rng), (40, 80, 150)
                else:
                    lines, tint = otc_box(spec["name"], spec["license_number"], form, rng), (150, 90, 40)
            path = OUT / f"{case['id']}_{i}_{slot}_{slug(spec['name'])}.png"
            render(lines, seed=hash(path.name) & 0xFFFF, tint=tint).save(path)
            written += 1
        print(f"{case['id']:<32} {len(order)} 張　{case['story']}")
    print(f"\n寫出 {written} 張到 {OUT}")


if __name__ == "__main__":
    main()
