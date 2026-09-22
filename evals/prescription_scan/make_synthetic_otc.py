#!/usr/bin/env python3
"""產生合成的台灣成藥外盒，補真實照片缺的那一組。

  python evals/prescription_scan/make_synthetic_otc.py

Wikimedia Commons 上找得到的成藥照片幾乎全是國外包裝（芬蘭普拿疼、美國撒隆
巴斯、香港白花油），台灣長輩實際會從藥局帶回家的普拿疼、伏冒、斯斯、五分珠
一張都沒有。要量「釘選藥證後相衝偵測會不會有東西可比」，需要的是**品名與
藥證字號都對得上 drug_catalog.json** 的包裝，只好自己畫。

品名、藥證字號、成分與劑型全部取自 resources/drug_catalog.json 的真實藥證；
廠商名以「○○」代替、批號與效期虛構。版型照台灣成藥外盒常見的正面排法：
品牌、品名、規格、成分、用法用量、字號。報告裡這組以 synthetic 分開列。

選品的理由是要讓四條相衝規則各有能觸發的一側：
  成分重複   — 普拿疼加強錠、伏冒鼻炎感冒錠、友露安液（都含乙醯胺酚）
  抗膽鹼疊加 — 伏冒鼻炎感冒錠（CHLORPHENIRAMINE）× 暈車船液（DIMENHYDRINATE）
  出血       — 五分珠散（ASPIRIN, N02BA01）、斯斯感冒膠囊（ETHENZAMIDE, N02BA07）、
               普拿克必理痛（IBUPROFEN, M01AE01）
  外用       — 撒隆巴斯貼片（含 DIPHENHYDRAMINE，貼片刻意不排除在比對外）
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


# 每一張：檔名 → (主色, 行列表)。行是 (字級, 文字, 對齊)。
# 品名／字號／成分照藥證庫原文，不要「修飾」成比較好讀的寫法——量的是
# 藥證庫比對，改字就量不到了。
BOXES: dict[str, tuple[str, list[tuple[int, str, str]]]] = {
    "otc_synth_panadol_extra.png": ("#c8102e", [
        (30, "PANADOL", "left"),
        (58, "普拿疼加強錠", "left"),
        (28, "Panadol Extra　20 錠", "left"),
        (22, "", "left"),
        (26, "每錠含：Acetaminophen 500mg、Caffeine 65mg", "left"),
        (26, "適用：緩解疼痛及退燒", "left"),
        (26, "用法用量：成人每次 1～2 錠，每 4～6 小時一次，一日不超過 8 錠", "left"),
        (22, "", "left"),
        (24, "衛署藥輸字第023623號", "left"),
        (22, "○○藥廠　批號 A2609　有效期限 2028.06", "left"),
    ]),
    "otc_synth_panadol_cold_sinus.png": ("#1f5fa8", [
        (30, "PANADOL", "left"),
        (54, "普拿疼伏冒鼻炎感冒錠", "left"),
        (28, "Panadol Cold & Flu Sinus　10 錠", "left"),
        (22, "", "left"),
        (24, "每錠含：Acetaminophen 500mg、Pseudoephedrine HCl 30mg、", "left"),
        (24, "　　　　Chlorpheniramine Maleate 2mg", "left"),
        (26, "適用：緩解鼻塞、流鼻水、打噴嚏、頭痛、發燒", "left"),
        (26, "用法用量：成人每次 1 錠，每 4～6 小時一次", "left"),
        (22, "", "left"),
        (24, "衛署藥輸字第023723號", "left"),
        (22, "○○藥廠　批號 B1107　有效期限 2027.11", "left"),
    ]),
    "otc_synth_susu_cold.png": ("#2a7d2e", [
        (30, "SUSU", "left"),
        (60, "斯斯感冒膠囊", "left"),
        (28, "30 粒", "left"),
        (22, "", "left"),
        (22, "每粒含：Acetaminophen 250mg、Ethenzamide 100mg、Caffeine Anhydrous 30mg、", "left"),
        (22, "　　　　Chlorpheniramine Maleate 2.5mg、dl-Methylephedrine HCl 20mg、", "left"),
        (22, "　　　　Codeine Phosphate 8mg", "left"),
        (26, "適用：感冒諸症狀（頭痛、發燒、咳嗽、鼻塞）之緩解", "left"),
        (26, "用法用量：成人每次 1 粒，一日 3 次，飯後服用", "left"),
        (22, "", "left"),
        (24, "衛署藥製字第032026號", "left"),
        (22, "○○製藥　批號 S0322　有效期限 2027.03", "left"),
    ]),
    "otc_synth_wufenzhu_powder.png": ("#8a5a00", [
        (60, "五分珠散", "center"),
        (28, "WU FEN CHU", "center"),
        (26, "12 包", "center"),
        (22, "", "left"),
        (24, "每包含：Aspirin 300mg、Acetaminophen 150mg、Caffeine Anhydrous 50mg", "left"),
        (26, "適用：頭痛、牙痛、發燒、神經痛", "left"),
        (26, "用法用量：成人每次 1 包，一日 3 次，飯後溫開水送服", "left"),
        (22, "", "left"),
        (24, "衛署藥製字第028569號", "left"),
        (22, "○○製藥　批號 W5108　有效期限 2027.08", "left"),
    ]),
    "otc_synth_ibuprofen_200.png": ("#d35400", [
        (30, "PANAKE", "left"),
        (48, "普拿克必理痛膜衣錠２００公絲（異布洛芬）", "left"),
        (28, "Ibuprofen 200mg　24 錠", "left"),
        (22, "", "left"),
        (26, "每錠含：Ibuprofen 200mg", "left"),
        (26, "適用：消炎、鎮痛、解熱", "left"),
        (26, "用法用量：成人每次 1～2 錠，每 4～6 小時一次，一日不超過 6 錠", "left"),
        (22, "", "left"),
        (24, "衛署藥製字第040828號", "left"),
        (22, "○○製藥　批號 P2204　有效期限 2028.02", "left"),
    ]),
    "otc_synth_motion_sickness_liquid.png": ("#6a1b9a", [
        (30, "風熱友", "left"),
        (52, "\"風熱友\" 暈車船液（氯茶/二苯安明）", "left"),
        (28, "30mL × 3 瓶", "left"),
        (22, "", "left"),
        (26, "每瓶含：Dimenhydrinate 50mg", "left"),
        (26, "適用：預防及緩解暈車、暈船、暈機引起之噁心、嘔吐、頭暈", "left"),
        (26, "用法用量：成人搭乘前 30 分鐘服用 1 瓶", "left"),
        (22, "", "left"),
        (24, "衛署藥製字第036452號", "left"),
        (22, "○○製藥　批號 M0930　有效期限 2027.09", "left"),
    ]),
    "otc_synth_ulan_liquid.png": ("#00695c", [
        (30, "三支雨傘標", "left"),
        (50, "\"大裕\" 三支雨傘標友露安液", "left"),
        (28, "60mL", "left"),
        (22, "", "left"),
        (22, "每 60mL 含：Acetaminophen 300mg、Chlorpheniramine Maleate 4mg、", "left"),
        (22, "　　　　　　dl-Methylephedrine HCl 30mg、Potassium Guaiacolsulfonate 150mg、", "left"),
        (22, "　　　　　　Caffeine Anhydrous 50mg", "left"),
        (26, "適用：感冒諸症狀之緩解", "left"),
        (26, "用法用量：成人每次 20mL，一日 3 次", "left"),
        (22, "", "left"),
        (24, "衛署藥製字第045350號", "left"),
        (22, "○○製藥　批號 U1122　有效期限 2027.11", "left"),
    ]),
    "otc_synth_salonpas_patch.png": ("#b71c1c", [
        (30, "SALONPAS", "left"),
        (64, "撒隆巴斯", "left"),
        (28, "貼片劑　20 片", "left"),
        (22, "", "left"),
        (22, "每 100g 含：Methyl Salicylate 6.3g、Menthol 5.7g、Camphor 1.2g、", "left"),
        (22, "　　　　　　Diphenhydramine 0.5g、Thymol、Borneol、Zinc Oxide", "left"),
        (26, "適用：肩膀酸痛、腰痛、肌肉痛、關節痛、跌打撲傷", "left"),
        (26, "用法用量：一日 1～2 次，貼於患部", "left"),
        (22, "", "left"),
        (24, "內衛成製字第000061號", "left"),
        (22, "○○製藥　批號 SP041　有效期限 2028.04", "left"),
    ]),
}


def render(accent: str, lines: list[tuple[int, str, str]], seed: int) -> Image.Image:
    """畫一個外盒正面：上方色帶放品牌，白底放內容，最後加輕微傾斜與雜訊。

    相機拍外盒不會是掃描件那麼平整，稍微轉個角度、加點模糊與雜訊，讓辨識
    面對的是接近手機照片的輸入，而不是排版軟體的輸出。
    """
    rng = random.Random(seed)
    width, pad = 1200, 56
    line_gap = 12
    heights = [_font(size).getbbox("國")[3] + line_gap for size, _, _ in lines]
    band = 150
    height = band + pad * 2 + sum(heights)
    img = Image.new("RGB", (width, height), "#fbfbf7")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width, band], fill=accent)
    # 色帶上放第一行（品牌），其餘照順序往下排
    y = (band - _font(lines[0][0]).getbbox("國")[3]) // 2
    for i, (size, text, align) in enumerate(lines):
        font = _font(size)
        if i == 0:
            draw.text((pad, y), text, font=font, fill="white")
            y = band + pad
            continue
        if align == "center":
            tw = draw.textlength(text, font=font)
            x = (width - tw) / 2
        else:
            x = pad
        draw.text((x, y), text, font=font, fill="#1a1a1a")
        y += heights[i]
    # 邊框：外盒的摺線
    draw.rectangle([2, 2, width - 3, height - 3], outline="#c9c9c0", width=3)
    # 傾斜、模糊、雜訊
    angle = rng.uniform(-1.6, 1.6)
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor="#d9d4c7")
    img = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.4, 0.9)))
    px = img.load()
    for _ in range(int(img.width * img.height * 0.004)):
        x, y = rng.randrange(img.width), rng.randrange(img.height)
        r, g, b = px[x, y]
        d = rng.randint(-18, 18)
        px[x, y] = (max(0, min(255, r + d)), max(0, min(255, g + d)), max(0, min(255, b + d)))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (name, (accent, lines)) in enumerate(BOXES.items()):
        render(accent, lines, seed=100 + i).save(OUT / name)
        print("寫出", OUT / name)


if __name__ == "__main__":
    main()
