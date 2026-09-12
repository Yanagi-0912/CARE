#!/usr/bin/env python3
"""把公開的單字手寫資料集拼成文句影像，產生可直接評測的 golden.jsonl。

  # 下限：乾淨的繁體手寫單字（AI.FREE Team，台灣，CC BY-NC-SA 4.0）
  python scripts/handwriting_corpus.py --source aifree --lines 30

  # 上限：草書書法字（NCCU VIP Lab，MIT）
  python scripts/handwriting_corpus.py --source cursive --lines 30

  # 兩組一起產，接著直接評測
  python scripts/handwriting_corpus.py --source both --lines 30
  python scripts/handwriting_eval.py --golden evals/handwriting/aifree/golden.jsonl

這是「合成」不是「真跡」，界線要講清楚：每個字都是獨立影像等距拼起來的，
沒有連筆、沒有自然的基線漂移、沒有一個人前後一致的筆壓與字形。所以它問的
不是「模型讀不讀得懂長輩的字」，而是一個更基本、但必須先過的問題——

  乾淨、工整、逐字分離的繁體中文手寫，模型讀得準嗎？

aifree 組回答的就是這一題，而且形態相符，結論可以直接採信。

cursive 組**不是**「同一題的難版」，別把兩組當成同一條難度軸的兩端。草書是
另一套字形規範，模型讀錯是因為認不得這套符號系統；長輩手寫的失真來源是運動
控制退化——手抖、筆畫變小、力道不穩，字形規範本身沒變。實測也印證了這點：
模型把「腳有點腫按下去會凹」讀成「秋水點點梅花落四」，是在往「書法作品該有的
樣子」猜。碰到血壓紀錄它不會這樣錯，但會換一種先驗去補，未必比較安全。

所以 cursive 組的 CER 不能當長輩手寫的預期值，它量的是另一件事。真實長輩
手寫目前沒有任何公開資料集，只能自己蒐集——請長輩照抄事先備好的稿子，稿子
本身就是 ground truth，既免標註也不涉個資。

兩個來源都沒有阿拉伯數字與標點，所以句子一律用中文數字。這跟長輩實際會寫的
「138/82」不同——而血壓數值恰好是最不能錯的欄位，這兩組完全沒測到。

歷次評測結果收斂在 evals/handwriting/baselines.json。
"""

from __future__ import annotations

import argparse
import io
import json
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image, ImageOps

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_EVAL_ROOT = _PROJECT_ROOT / "evals" / "handwriting"
_CACHE = _EVAL_ROOT / ".cache"

AIFREE_ZIPS = [
    "https://raw.githubusercontent.com/AI-FREE-Team/Traditional-Chinese-Handwriting-Dataset"
    f"/master/data/cleaned_data(50_50)-20200420T071507Z-{n}.zip"
    for n in ("001", "002", "003", "004")
]
AIFREE_ENTRY = re.compile(r"cleaned_data\(50_50\)/(.+)_(\d+)\.png$")

CURSIVE_RAW = (
    "https://raw.githubusercontent.com/nccuviplab/CursiveChineseCalligraphyDataset"
    "/master/Cursive_Chinese_Calligraphy_Dataset/Test"
)
CURSIVE_REPO = "nccuviplab/CursiveChineseCalligraphyDataset"
# 同一個字在草書裡有多種寫法，資料集用尾綴數字分開放（例：七、七2、七3）。
# 標籤是去掉尾綴後的字本身。
CURSIVE_SUFFIX = re.compile(r"^(.+?)\d*$")

# 句子刻意貼近 CARE 實際會收到的內容：血壓、血糖、用藥、症狀自述。
# 全部使用中文數字——兩個資料集都沒有阿拉伯數字。
SENTENCES = [
    "今日早上收縮壓一百三十八",
    "舒張壓八十二脈搏七十二",
    "晚上量血壓一百四十二",
    "飯前血糖一百二十六",
    "飯後兩小時血糖一百八十九",
    "早上飯後吃降血壓的藥",
    "睡前忘記吃藥",
    "今天頭有點暈沒有吃藥",
    "早晚各吃一顆共兩次",
    "血壓計顯示一百五十九",
    "昨天晚上睡不好",
    "今日量了三次血壓",
    "醫生說要每天早上量",
    "藥剩下五天的份量",
    "下星期三回診",
    "胸口有點悶不太舒服",
    "早上七點量的血壓",
    "血糖比昨天高很多",
    "最近走路會喘",
    "腳有點腫按下去會凹",
    "今天沒有頭暈",
    "血壓一百三十上下",
    "吃完飯後量的血糖",
    "藥吃完了要去拿藥",
    "晚上十點吃的藥",
    "早餐前量血糖九十八",
    "手會抖寫字很慢",
    "這幾天血壓都偏高",
    "醫生開了新的藥",
    "今天有按時吃藥",
]


def _download(url: str, dest: Path) -> Path:
    """下載並快取。已存在就直接沿用——這些檔案是不可變的發佈版本。"""
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  下載 {url.rsplit('/', 1)[-1]} …", flush=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=300) as response, tmp.open("wb") as fh:
            while chunk := response.read(1 << 20):
                fh.write(chunk)
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"下載失敗 {url}：{exc}") from exc
    tmp.replace(dest)
    return dest


class GlyphSource:
    """依字取出一張手寫字影像。"""

    name: str
    license: str

    def glyph(self, char: str, rng: random.Random) -> Optional[Image.Image]:
        raise NotImplementedError

    def available(self, chars: set[str]) -> set[str]:
        raise NotImplementedError


class AiFreeSource(GlyphSource):
    name = "aifree"
    license = "CC BY-NC-SA 4.0 (AI.FREE Team / 南臺科大)"

    def __init__(self) -> None:
        self._index: dict[str, list[tuple[Path, str]]] = {}
        for url in AIFREE_ZIPS:
            path = _download(url, _CACHE / "aifree" / url.rsplit("/", 1)[-1])
            with zipfile.ZipFile(path) as archive:
                for entry in archive.namelist():
                    matched = AIFREE_ENTRY.match(entry)
                    if matched:
                        self._index.setdefault(matched.group(1), []).append((path, entry))
        self._handles: dict[Path, zipfile.ZipFile] = {}
        print(f"  AI.FREE：{len(self._index)} 字種／{sum(len(v) for v in self._index.values())} 張")

    def available(self, chars: set[str]) -> set[str]:
        return {c for c in chars if c in self._index}

    def glyph(self, char: str, rng: random.Random) -> Optional[Image.Image]:
        entries = self._index.get(char)
        if not entries:
            return None
        path, entry = rng.choice(entries)
        handle = self._handles.get(path)
        if handle is None:
            handle = self._handles[path] = zipfile.ZipFile(path)
        return Image.open(io.BytesIO(handle.read(entry))).convert("L")


class CursiveSource(GlyphSource):
    name = "cursive"
    license = "MIT (NCCU VIP Lab)"

    def __init__(self) -> None:
        # 整個 Test 子樹一次拉完（9,548 張／13,829 個節點）。逐字去問 contents API
        # 會是上百次請求，未認證的 GitHub 每小時只給 60 次，必然中途失敗。
        self._index: dict[str, list[str]] = {}
        for path in self._tree():
            folder, _, _ = path.partition("/")
            matched = CURSIVE_SUFFIX.match(folder)
            if matched:
                self._index.setdefault(matched.group(1), []).append(path)
        print(f"  草書：{len(self._index)} 字種／{sum(len(v) for v in self._index.values())} 張")

    @staticmethod
    def _api(url: str) -> object:
        request = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise SystemExit(
                    "GitHub API 已達速率上限，請稍後再試（未認證每小時 60 次）。"
                ) from exc
            raise SystemExit(f"GitHub API 失敗（{exc.code}）：{url}") from exc

    def _tree(self) -> list[str]:
        listing = self._api(
            f"https://api.github.com/repos/{CURSIVE_REPO}/contents"
            "/Cursive_Chinese_Calligraphy_Dataset?ref=master"
        )
        sha = next(
            (item["sha"] for item in listing if item.get("name") == "Test"), None
        )
        if sha is None:
            raise SystemExit("草書資料集找不到 Test 目錄，倉庫結構可能已變動。")
        tree = self._api(
            f"https://api.github.com/repos/{CURSIVE_REPO}/git/trees/{sha}?recursive=1"
        )
        if tree.get("truncated"):
            raise SystemExit("GitHub 回傳的檔案樹被截斷，無法建立完整索引。")
        return [e["path"] for e in tree["tree"] if e["type"] == "blob"]

    def available(self, chars: set[str]) -> set[str]:
        return {c for c in chars if c in self._index}

    def glyph(self, char: str, rng: random.Random) -> Optional[Image.Image]:
        paths = self._index.get(char)
        if not paths:
            return None
        path = rng.choice(paths)
        cached = _CACHE / "cursive" / path
        _download(f"{CURSIVE_RAW}/{urllib.parse.quote(path)}", cached)
        return Image.open(cached).convert("L")


def normalize_glyph(glyph: Image.Image) -> Image.Image:
    """統一成白底黑字，並保留原本的方形字身框。

    兩個來源的極性相反：AI.FREE 是白底黑字，草書是黑底白字。不統一就會拼出
    一排黑色方塊。

    這裡刻意「不」裁到筆畫外框。中文字共用一個方形字身框，「一」只佔框中間
    薄薄一條、「壓」佔滿整格——裁掉留白再各自放大到同高，「一」就會變成一根
    貫穿整行的黑槓。保留字身框，字與字的相對大小才是對的。
    """
    if _mean_pixel(glyph) < 128:
        glyph = ImageOps.invert(glyph)
    return glyph


def _mean_pixel(glyph: Image.Image) -> float:
    histogram = glyph.histogram()
    total = sum(histogram)
    return sum(i * n for i, n in enumerate(histogram)) / total if total else 255.0


def compose_line(
    text: str,
    source: GlyphSource,
    rng: random.Random,
    *,
    glyph_height: int,
    margin: int,
) -> Optional[Image.Image]:
    """把逐字影像橫向拼成一行。

    刻意加入字距、基線與大小的隨機抖動。等距對齊的網格會讓模型可以靠版面
    切字，那是合成影像才有的便宜——真實手寫沒有這條線索，留著會高估分數。
    """
    glyphs: list[Image.Image] = []
    for char in text:
        raw = source.glyph(char, rng)
        if raw is None:
            return None
        glyph = normalize_glyph(raw)
        scale = rng.uniform(0.88, 1.06)
        side = max(8, int(glyph_height * scale))
        width = max(8, int(glyph.width * side / glyph.height))
        glyphs.append(glyph.resize((width, side), Image.LANCZOS))

    gaps = [rng.randint(0, int(glyph_height * 0.10)) for _ in glyphs]
    jitters = [rng.randint(-int(glyph_height * 0.08), int(glyph_height * 0.08)) for _ in glyphs]

    total_width = sum(g.width for g in glyphs) + sum(gaps) + margin * 2
    max_height = max(g.height for g in glyphs)
    canvas_height = max_height + margin * 2 + int(glyph_height * 0.16)
    canvas = Image.new("L", (total_width, canvas_height), color=255)

    x = margin
    baseline = margin + int(glyph_height * 0.08)
    for glyph, gap, jitter in zip(glyphs, gaps, jitters):
        y = baseline + (max_height - glyph.height) // 2 + jitter
        canvas.paste(glyph, (x, max(0, y)))
        x += glyph.width + gap
    return canvas


def build(
    source: GlyphSource,
    *,
    lines: int,
    out_dir: Path,
    glyph_height: int,
    seed: int,
) -> int:
    rng = random.Random(seed)
    needed = {ch for sentence in SENTENCES for ch in sentence}
    have = source.available(needed)
    missing = needed - have
    usable = [s for s in SENTENCES if all(ch in have for ch in s)]

    print(f"\n[{source.name}] 授權：{source.license}")
    print(f"  字元覆蓋：{len(have)}/{len(needed)}｜可用句子：{len(usable)}/{len(SENTENCES)}")
    if missing:
        print(f"  缺字（句子已自動排除）：{''.join(sorted(missing))}")
    if not usable:
        print("  ✗ 沒有任何句子的字全部齊備，無法產生。")
        return 0

    samples_dir = out_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for index in range(lines):
        text = usable[index % len(usable)]
        image = compose_line(text, source, rng, glyph_height=glyph_height, margin=int(glyph_height * 0.3))
        if image is None:
            continue
        name = f"{index:03d}.png"
        image.save(samples_dir / name)
        rows.append({"image": f"samples/{name}", "text": text})

    golden = out_dir / "golden.jsonl"
    golden.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    print(f"  ✓ 產生 {len(rows)} 張 → {golden.relative_to(_PROJECT_ROOT)}")
    return len(rows)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", choices=("aifree", "cursive", "both"), default="both")
    parser.add_argument("--lines", type=int, default=30, help="每個來源產生幾張")
    parser.add_argument("--glyph-height", type=int, default=96, help="單字放大後的高度（px）")
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--out-root", type=Path, default=_EVAL_ROOT)
    args = parser.parse_args(argv)

    names = ("aifree", "cursive") if args.source == "both" else (args.source,)
    total = 0
    for name in names:
        print(f"\n=== 準備 {name} ===")
        source: GlyphSource = AiFreeSource() if name == "aifree" else CursiveSource()
        total += build(
            source,
            lines=args.lines,
            out_dir=args.out_root / name,
            glyph_height=args.glyph_height,
            seed=args.seed,
        )

    if total:
        print("\n接著跑評測：")
        for name in names:
            rel = (args.out_root / name / "golden.jsonl").relative_to(_PROJECT_ROOT)
            print(f"  .venv/bin/python scripts/handwriting_eval.py --golden {rel}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
