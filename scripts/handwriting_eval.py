#!/usr/bin/env python3
"""量測目前接的視覺模型在中文手寫上的實際表現。

兩種模式，先跑哪一種取決於你有沒有標註：

  # 沒有標註也能跑：同一張圖問 N 次，看答案穩不穩
  python scripts/handwriting_eval.py --self-consistency

  # 有標註：算 CER、幻覺率、棄權率
  python scripts/handwriting_eval.py --golden evals/handwriting/golden.jsonl

  # 換模型比較（不影響 MODEL_NAME，不會動到 RAG）
  python scripts/handwriting_eval.py --compare gemini-2.5-flash,gemini-2.5-pro \
      --golden evals/handwriting/golden.jsonl --out /tmp/hw-report.json

自我一致性之所以放在第一位，是因為它不需要任何標註就能給出訊號：模型對同一張
影像每次講的都不一樣，代表它在猜；每次都一樣但錯，標註才分得出來。前者可以在
你動手標一百張之前就先發現。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.core.config import settings
from app.services.gemini import GeminiService
from app.services.gemini.shared.errors import GeminiError

DEFAULT_GOLDEN = _PROJECT_ROOT / "evals" / "handwriting" / "golden.jsonl"

# 看不懂的字要求模型填這個記號，而不是猜一個字。棄權率是本評測最重要的
# 指標之一：文獻上模型在中文手寫的主要失效模式不是讀不出來，是讀不出來卻
# 給出一段語意通順、跟原文無關的文字。
ILLEGIBLE_MARK = "■"

TRANSCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "legible": {"type": "boolean"},
    },
    "required": ["text", "legible"],
}

TRANSCRIPTION_PROMPT = f"""請逐字轉錄這張影像上的手寫中文。

規則：
1. 只轉錄實際寫出來的字。不要推測、不要補齊、不要修正錯字或別字。
2. 任何一個字你無法確定是什麼，就填一個 {ILLEGIBLE_MARK}，不要猜一個看起來合理的字。
3. 保留原本的換行；不要重新排版、不要加標點。
4. 如果整張影像你都判讀不出來，legible 填 false，text 留空字串。

只輸出符合 schema 的結果。"""

_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
}


def _mime_for(path: Path) -> str:
    mime = _MIME_BY_SUFFIX.get(path.suffix.lower())
    if mime is None:
        raise SystemExit(f"不支援的影像副檔名：{path}")
    return mime


def levenshtein(a: str, b: str) -> int:
    """字元級編輯距離。中文逐字比對，不做斷詞。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    """字元錯誤率。參考字串為空時，只要模型有輸出就記 1.0。"""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return levenshtein(reference, hypothesis) / len(reference)


def _normalize(text: str) -> str:
    """比對前只去掉空白與換行——標點與繁簡差異都算錯，因為那正是要量的東西。"""
    return "".join(text.split())


@dataclass
class Attempt:
    text: str
    legible: bool
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class CaseResult:
    image: str
    reference: Optional[str]
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def primary(self) -> Optional[Attempt]:
        for attempt in self.attempts:
            if attempt.ok:
                return attempt
        return None

    @property
    def cer(self) -> Optional[float]:
        if self.reference is None or self.primary is None:
            return None
        return cer(_normalize(self.reference), _normalize(self.primary.text))

    @property
    def instability(self) -> Optional[float]:
        """同一張影像多次作答之間的平均兩兩 CER。越高代表模型越是在猜。"""
        texts = [_normalize(a.text) for a in self.attempts if a.ok]
        if len(texts) < 2:
            return None
        pairs = [
            cer(texts[i], texts[j])
            for i in range(len(texts))
            for j in range(i + 1, len(texts))
        ]
        return statistics.mean(pairs)

    @property
    def abstain_ratio(self) -> Optional[float]:
        """輸出裡標成「看不懂」的字元占比。長期為 0 代表模型從不承認讀不出來。"""
        primary = self.primary
        if primary is None:
            return None
        text = _normalize(primary.text)
        if not text:
            return 1.0 if not primary.legible else 0.0
        return text.count(ILLEGIBLE_MARK) / len(text)

    @property
    def length_ratio(self) -> Optional[float]:
        """輸出長度／正解長度。明顯 >1 通常就是模型自己編了一段。"""
        if self.reference is None or self.primary is None:
            return None
        ref = _normalize(self.reference)
        if not ref:
            return None
        return len(_normalize(self.primary.text)) / len(ref)


def vocabulary_hint(references: Sequence[str]) -> str:
    """把語料用到的字集列給模型。

    這是「限定候選集」在轉錄這一層的最小形式：不給句子（那會退化成選擇題），
    只告訴模型這批影像的字元來自哪個封閉集合。CARE 真正上線時對應的是藥品
    目錄、數值範圍這類本來就已知的約束——同樣是把開放式辨識收斂成有界問題。
    """
    charset = sorted({ch for ref in references for ch in ref})
    return (
        "\n\n這批影像的用字全部落在下列字集內，轉錄結果不應出現集合以外的字；"
        f"若某字看起來不在集合內，代表你認錯了，請改填 {ILLEGIBLE_MARK}。\n"
        + "".join(charset)
    )


async def _transcribe(
    service: GeminiService,
    image_bytes: bytes,
    mime_type: str,
    timeout: float,
    prompt: str,
) -> Attempt:
    """單次轉錄。逾時一律記成該次失敗，不讓整批評測停在一個請求上。

    底層 SDK 遇到 429 會自行退避重試，沒有上限的話一個被限流的請求可以把整
    批拖到看不出還在不在跑——實測就這樣卡了十幾分鐘而沒有任何輸出。
    """
    try:
        payload = await asyncio.wait_for(
            service.invoke_structured_output_with_image(
                prompt=prompt,
                image_bytes=image_bytes,
                mime_type=mime_type,
                json_schema=TRANSCRIPTION_SCHEMA,
            ),
            timeout=timeout,
        )
    except (GeminiError, asyncio.TimeoutError) as exc:
        return Attempt(text="", legible=False, error=f"{type(exc).__name__}: {exc}")
    if not isinstance(payload, dict):
        return Attempt(text="", legible=False, error="回應不是物件")
    return Attempt(
        text=str(payload.get("text") or ""),
        legible=bool(payload.get("legible")),
    )


async def run_model(
    *,
    model_name: str,
    cases: Sequence[tuple[Path, Optional[str]]],
    repeats: int,
    temperature: float,
    concurrency: int,
    call_timeout: float,
    prompt: str,
) -> list[CaseResult]:
    service = GeminiService(
        api_key=settings.GEMINI_API_KEY,
        model_name=model_name,
        temperature=temperature,
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def one(path: Path, reference: Optional[str]) -> CaseResult:
        image_bytes = path.read_bytes()
        mime_type = _mime_for(path)
        result = CaseResult(image=str(path.relative_to(_PROJECT_ROOT)), reference=reference)
        for _ in range(repeats):
            async with semaphore:
                result.attempts.append(
                    await _transcribe(service, image_bytes, mime_type, call_timeout, prompt)
                )
        done = len(result.attempts)
        print(f"  [{model_name}] {result.image} ({done}/{repeats})", flush=True)
        return result

    return await asyncio.gather(*(one(p, r) for p, r in cases))


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return statistics.mean(present) if present else None


def summarize(results: Sequence[CaseResult], hallucination_cer: float) -> dict[str, Any]:
    cers = [r.cer for r in results]
    scored = [c for c in cers if c is not None]
    return {
        "cases": len(results),
        "failed_calls": sum(1 for r in results if r.primary is None),
        "cer_mean": _mean(cers),
        "cer_median": statistics.median(scored) if scored else None,
        "exact_match": (
            sum(1 for c in scored if c == 0) / len(scored) if scored else None
        ),
        # CER 高到這個程度時，輸出已經不是「有錯字的轉錄」而是另一段文字。
        "hallucination_rate": (
            sum(1 for c in scored if c > hallucination_cer) / len(scored)
            if scored
            else None
        ),
        "instability_mean": _mean([r.instability for r in results]),
        "abstain_ratio_mean": _mean([r.abstain_ratio for r in results]),
        "length_ratio_mean": _mean([r.length_ratio for r in results]),
    }


def _fmt(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.3f}"


def print_report(model_name: str, results: Sequence[CaseResult], summary: dict[str, Any]) -> None:
    print(f"\n=== {model_name} ===")
    print(f"{'影像':<34}{'CER':>8}{'不穩定':>8}{'棄權':>8}{'長度比':>8}")
    for r in sorted(results, key=lambda x: (x.cer is None, -(x.cer or 0))):
        name = r.image if len(r.image) <= 33 else "…" + r.image[-32:]
        print(
            f"{name:<34}{_fmt(r.cer):>8}{_fmt(r.instability):>8}"
            f"{_fmt(r.abstain_ratio):>8}{_fmt(r.length_ratio):>8}"
        )
        if r.primary is None:
            print(f"    ✗ 全部呼叫失敗：{r.attempts[0].error if r.attempts else 'no attempt'}")

    print(f"\n  樣本數            {summary['cases']}")
    print(f"  呼叫全失敗        {summary['failed_calls']}")
    print(f"  CER 平均          {_fmt(summary['cer_mean'])}")
    print(f"  CER 中位數        {_fmt(summary['cer_median'])}")
    print(f"  完全正確率        {_fmt(summary['exact_match'])}")
    print(f"  幻覺率            {_fmt(summary['hallucination_rate'])}")
    print(f"  自我不一致        {_fmt(summary['instability_mean'])}")
    print(f"  棄權字元比        {_fmt(summary['abstain_ratio_mean'])}")
    print(f"  輸出長度比        {_fmt(summary['length_ratio_mean'])}")


def load_cases(
    golden: Optional[Path], samples_dir: Path
) -> list[tuple[Path, Optional[str]]]:
    if golden is not None:
        cases: list[tuple[Path, Optional[str]]] = []
        for line_no, line in enumerate(
            golden.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{golden}:{line_no} 不是合法 JSON：{exc}") from exc
            image = (golden.parent / row["image"]).resolve()
            if not image.is_file():
                raise SystemExit(f"{golden}:{line_no} 找不到影像：{image}")
            cases.append((image, row.get("text")))
        if not cases:
            raise SystemExit(f"{golden} 沒有任何案例")
        return cases

    images = sorted(
        p
        for p in samples_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _MIME_BY_SUFFIX
    )
    if not images:
        raise SystemExit(
            f"{samples_dir} 裡沒有影像。把樣本放進去，或用 --golden 指定標註檔。"
        )
    return [(p, None) for p in images]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden", type=Path, default=None, help="標註檔（JSONL）")
    parser.add_argument(
        "--samples",
        type=Path,
        default=_PROJECT_ROOT / "evals" / "handwriting" / "samples",
        help="無標註時要掃描的影像資料夾",
    )
    parser.add_argument(
        "--compare",
        default=None,
        help="逗號分隔的模型清單；預設只跑 settings.MODEL_NAME",
    )
    parser.add_argument(
        "--self-consistency",
        action="store_true",
        help="同一張圖問多次量穩定度（不需標註）",
    )
    parser.add_argument("--repeats", type=int, default=None, help="每張圖呼叫次數")
    parser.add_argument("--temperature", type=float, default=None, help="取樣溫度")
    parser.add_argument("--concurrency", type=int, default=4, help="並發請求數")
    parser.add_argument(
        "--call-timeout", type=float, default=90.0, help="單次呼叫逾時秒數（預設 90）"
    )
    parser.add_argument(
        "--constrain-vocab",
        action="store_true",
        help="把標註用到的字集寫進提示詞（限定候選集）；需要 --golden",
    )
    parser.add_argument(
        "--hallucination-cer",
        type=float,
        default=0.5,
        help="CER 超過此值即計為幻覺（預設 0.5）",
    )
    parser.add_argument("--out", type=Path, default=None, help="輸出 JSON 報告")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="任一模型 CER 平均高於此值就以非 0 結束（給 CI 用）",
    )
    args = parser.parse_args(argv)

    if not settings.GEMINI_API_KEY:
        raise SystemExit("GEMINI_API_KEY 未設定（.env）")

    golden = args.golden
    if golden is None and DEFAULT_GOLDEN.is_file():
        golden = DEFAULT_GOLDEN
    if golden is not None and not golden.is_file():
        raise SystemExit(f"找不到標註檔：{golden}")

    # 量穩定度必須讓模型有機會給出不同答案，所以預設拉高溫度並重複呼叫；
    # 一般評測則沿用正式路徑的 temperature=0，單次呼叫。
    repeats = args.repeats if args.repeats is not None else (5 if args.self_consistency else 1)
    temperature = (
        args.temperature
        if args.temperature is not None
        else (0.7 if args.self_consistency else 0.0)
    )

    cases = load_cases(golden, args.samples)

    prompt = TRANSCRIPTION_PROMPT
    if args.constrain_vocab:
        references = [ref for _, ref in cases if ref]
        if not references:
            raise SystemExit("--constrain-vocab 需要有標註的案例")
        prompt += vocabulary_hint(references)
        print(f"限定字集：{len({c for r in references for c in r})} 字")
    models = (
        [m.strip() for m in args.compare.split(",") if m.strip()]
        if args.compare
        else [settings.MODEL_NAME]
    )

    print(f"樣本 {len(cases)} 張｜模型 {', '.join(models)}｜每張呼叫 {repeats} 次｜temperature={temperature}")
    if golden is None:
        print("（無標註：只會產出自我不一致與棄權率，CER 欄位留空）")

    report: dict[str, Any] = {
        "models": {},
        "repeats": repeats,
        "temperature": temperature,
        "golden": str(golden) if golden else None,
        # 條件要跟數字存在一起。同一組影像在「無約束」與「限定字集」下的 CER
        # 差六倍，報告若不記下自己是哪一種，事後就分不出來了。
        "constrained_vocab": bool(args.constrain_vocab),
        "cases": len(cases),
    }
    worst_cer = None

    for model_name in models:
        results = asyncio.run(
            run_model(
                model_name=model_name,
                cases=cases,
                repeats=repeats,
                temperature=temperature,
                concurrency=args.concurrency,
                call_timeout=args.call_timeout,
                prompt=prompt,
            )
        )
        summary = summarize(results, args.hallucination_cer)
        print_report(model_name, results, summary)
        report["models"][model_name] = {
            "summary": summary,
            "cases": [
                {
                    "image": r.image,
                    "reference": r.reference,
                    "predictions": [a.text for a in r.attempts],
                    "errors": [a.error for a in r.attempts if a.error],
                    "cer": r.cer,
                    "instability": r.instability,
                    "abstain_ratio": r.abstain_ratio,
                    "length_ratio": r.length_ratio,
                }
                for r in results
            ],
        }
        if summary["cer_mean"] is not None:
            worst_cer = summary["cer_mean"] if worst_cer is None else max(worst_cer, summary["cer_mean"])

    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n報告已寫入 {args.out}")

    if args.fail_under is not None and worst_cer is not None and worst_cer > args.fail_under:
        print(f"\n✗ CER 平均 {worst_cer:.3f} 高於門檻 {args.fail_under}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
