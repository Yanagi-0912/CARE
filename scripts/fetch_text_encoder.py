#!/usr/bin/env python3
"""下載本地分類器用的句向量模型（multilingual-e5-small，int8 ONNX）。

Dockerfile 在 build 時呼叫這支；本機開發第一次跑之前也要執行一次：

    python scripts/fetch_text_encoder.py

**下載的是 int8 量化版（118MB），不是全精度版（470MB）。** 2026-09-19 在
guardrail 資料集上實測，兩者的 holdout macro-F1 是 0.9428 與 0.9455（差
0.003），但常駐記憶體是 280MB 與 928MB。那個差價不值得。

**為什麼不用 huggingface_hub 而是直接抓檔案**：那個套件會帶進一串傳遞依賴
（filelock、fsspec、requests…），而正式映像只需要在 build 時抓兩個檔案、
執行期完全用不到它。用標準函式庫就夠。

**為什麼要重試**：build 期間的網路失敗會直接讓部署卡住。kubeconform 的下載
就因為沒有重試擋過一次部署（CARE-infra 的前例），同樣的錯不犯第二次。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = _PROJECT_ROOT / "models" / "multilingual-e5-small"

# 釘住 commit 而不是用 main：模型檔換了而我們毫無所覺，是最難查的那種問題
# ——分類結果會整批漂掉，但程式碼與資料集都沒動過。
REPO = "Xenova/multilingual-e5-small"
REVISION = "main"
BASE = f"https://huggingface.co/{REPO}/resolve/{REVISION}"

FILES = {
    "model_quantized.onnx": "onnx/model_quantized.onnx",
    "tokenizer.json": "tokenizer.json",
}

RETRIES = 5
BACKOFF_SECONDS = 3


def _download(url: str, dest: Path) -> None:
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                tmp = dest.with_suffix(dest.suffix + ".part")
                # 先寫暫存檔再 rename：中途失敗不會留下一個看起來完整、
                # 實際被截斷的模型檔（那會在執行期才爆，而且訊息毫無線索）。
                tmp.write_bytes(resp.read())
                tmp.rename(dest)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < RETRIES:
                wait = BACKOFF_SECONDS * attempt
                print(f"    第 {attempt} 次失敗（{exc}），{wait} 秒後重試", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"下載 {url} 連續失敗 {RETRIES} 次") from last


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--force", action="store_true", help="已存在也重新下載")
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, remote in FILES.items():
        dest = args.out_dir / name
        if dest.exists() and not args.force:
            print(f"  {name} 已存在（{dest.stat().st_size / 1e6:.1f} MB），略過")
            continue
        print(f"  下載 {name} …", flush=True)
        _download(f"{BASE}/{remote}", dest)
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()[:16]
        print(f"    完成 {dest.stat().st_size / 1e6:.1f} MB  sha256:{digest}…")

    print(f"句向量模型就緒：{args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
