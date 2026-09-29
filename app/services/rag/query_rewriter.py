"""查詢改寫：一次呼叫產出知識庫重查與網搜兩路要用的查詢。"""

from __future__ import annotations

import importlib.util
import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from app.core.request_logging import log_stage
from app.services.gemini import GeminiService

logger = logging.getLogger(__name__)

# 改寫呼叫用的 thinking 等級。gemini-3.8-flash 關不掉 thinking：官方文件只
# 列 low／medium／high（預設 medium），`thinking_level="minimal"` 會回 400；
# `thinking_budget` 已不在 Gemini 3 的文件裡，實測行為也不一致。low 是有文件
# 保證的最低檔。
#
# 2026-09-12 實測（結構化輸出、同一組 prompt）：預設 2.1-4.6 秒，low 1.2-3.3
# 秒（共 9 次），6 題的改寫內容兩者幾乎相同。支援 minimal 的 3.6-flash／
# 3.5-flash-lite 更快（0.9-1.7 秒），但中文病名不標準（「持續性性興奮亢進症」
# 「持續性性喚起症候群」），而 gov.tw 搜尋吃的就是標準病名，所以不換模型。
REWRITE_THINKING_LEVEL = "low"

REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kb_query": {"type": "string"},
        "zh_terms": {"type": "string"},
        "en_terms": {"type": "string"},
        "typo_from": {"type": "string"},
        "typo_to": {"type": "string"},
    },
    "required": ["kb_query", "zh_terms", "en_terms", "typo_from", "typo_to"],
}

# 一次更正最多換幾個字。這是判斷、不是量測：觸發這項功能的線上實例（圓錐膠膜）
# 只錯一個字；同音卻換掉三個字以上，比較像是換成了另一個詞，而不是選錯字。
# 放寬前先收集被這條擋掉的實例（log 的 stage=rag_typo_fix outcome=rejected）。
_MAX_TYPO_CHARS = 2

_MAX_ZH_TERMS = 3
_TERM_SEPARATORS = re.compile(r"[,，、;；\s]+")
_ASCII_TOKEN = re.compile(r"^[A-Za-z0-9.\-]+$")


@dataclass(frozen=True)
class RewrittenQuery:
    """一次改寫的三種用途。

    - kb_query：CRAG 判 ambiguous 時重查知識庫的問句
    - zh_terms：網搜中文那一路（gov.tw）的關鍵字；空字串＝沿用原句
    - en_terms：網搜英文那一路（nih.gov 等）的關鍵字；空字串＝不搜英文
    - typo_fix：(原句裡的錯字詞, 更正後的詞)，只有 `accept_typo_fix` 確認過是同音
      錯字才有值。有值時網搜那段改用更正後的問句生成，並在答案開頭告訴使用者。
    """

    kb_query: str
    zh_terms: str = ""
    en_terms: str = ""
    typo_fix: tuple[str, str] | None = None


class QueryRewriter(Protocol):
    async def rewrite(self, query: str, docs: list[Document]) -> RewrittenQuery: ...


def normalize_zh_terms(raw: str) -> str:
    """最多三個關鍵字、以空白分隔，並拿掉純英數的字詞。

    拿掉英數字詞不能只靠 prompt 規則：實測「持續性性興奮症候群 PGAD」在
    gov.tw 回 0 筆、拿掉 PGAD 後回 5 筆；縮寫在中文站也容易撞到無關字串
    （「PGAD」命中疾管署 PDF 裡的質體名 pGAD-HAX-1）。縮寫留給英文那一路。
    """
    tokens = [
        token
        for token in _TERM_SEPARATORS.split(raw or "")
        if token and not _ASCII_TOKEN.match(token)
    ]
    return " ".join(tokens[:_MAX_ZH_TERMS])


def normalize_en_terms(raw: str) -> str:
    """把各種分隔符統一成空白。

    不像中文那樣切詞計數：英文醫學名詞常是多字詞（knee osteoarthritis），
    以空白切開再截斷會把一個名詞切成兩半。
    """
    return " ".join(token for token in _TERM_SEPARATORS.split(raw or "") if token)


@lru_cache(maxsize=1)
def _pinyin_table() -> dict[int, str]:
    """pypinyin 的單字讀音表（碼位 → 「jiǎo,jué,…」）。

    直接載入 `pinyin_dict.py` 這一個檔案，不走 `import pypinyin`：套件入口會把
    詞組字典一起載進來，實測 +64 MiB，單字表只要 +14 MiB（2026-09-29，macOS）。
    backend 以前就因為頂層 import 大套件被 OOMKilled，所以也延到第一次用到才載。
    """
    spec = importlib.util.find_spec("pypinyin")
    if spec is None or spec.origin is None:
        raise ModuleNotFoundError("pypinyin")
    path = Path(spec.origin).parent / "pinyin_dict.py"
    module_spec = importlib.util.spec_from_file_location("_care_pinyin_dict", path)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module.pinyin_dict


def _toneless_readings(char: str) -> frozenset[str]:
    """一個字所有讀音去掉聲調後的集合；不是漢字（查不到）回空集合。

    去聲調是刻意的：注音輸入最常見的錯是選到同音不同調的字（膠 jiāo／角 jiǎo）。
    """
    raw = _pinyin_table().get(ord(char), "")
    readings = set()
    for reading in raw.split(","):
        stripped = "".join(
            c for c in unicodedata.normalize("NFD", reading) if not unicodedata.combining(c)
        )
        if stripped:
            readings.add(stripped)
    return frozenset(readings)


def accept_typo_fix(query: str, typo_from: str, typo_to: str) -> tuple[str, str] | None:
    """模型說「原句的 typo_from 是 typo_to 打錯」時，確認它真的只是同音錯字。

    改寫 prompt 的規則 1 禁止替使用者補病名（「頭暈、耳鳴」不能寫成梅尼爾氏症），
    這個函式負責讓「修錯字」不會變成繞過規則 1 的後門。必須全部成立才接受：

    - typo_from 真的出現在原句裡，且與 typo_to 字數相同（推論病名通常字數不同）
    - 換掉的字最多 `_MAX_TYPO_CHARS` 個，而且每一個都是漢字、新舊兩字有共同讀音
      （忽略聲調）。「胃痛」→「胃癌」字數相同只差一字，但痛 tòng／癌 ái 不同音，
      這是改病名、不是錯字，會被擋下。
    """
    typo_from, typo_to = (typo_from or "").strip(), (typo_to or "").strip()
    if not typo_from or typo_from == typo_to or typo_from not in query:
        return None
    if len(typo_from) != len(typo_to):
        return None
    changed = [(a, b) for a, b in zip(typo_from, typo_to) if a != b]
    if len(changed) > _MAX_TYPO_CHARS:
        return None
    for old, new in changed:
        old_readings, new_readings = _toneless_readings(old), _toneless_readings(new)
        if not old_readings or not new_readings or not (old_readings & new_readings):
            return None
    return typo_from, typo_to


class GeminiQueryRewriter:
    def __init__(
        self,
        gemini_service: GeminiService | None = None,
        *,
        max_chars_per_doc: int = 200,
        invoke_rewrite: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._gemini = gemini_service
        self._max_chars = max_chars_per_doc
        self._invoke_rewrite = invoke_rewrite

    async def rewrite(self, query: str, docs: list[Document]) -> RewrittenQuery:
        snippets: list[str] = []
        for doc in docs[:3]:
            text = (doc.page_content or "").strip().replace("\n", " ")
            snippets.append(text[: self._max_chars])
        # 規則 1、2 是量出來的失誤：沒有規則 1 時「長輩喘、腳腫」被改寫成
        # 「心臟衰竭」（替使用者下診斷），沒有規則 2 時查證型問題的主體
        # （某種偏方）被丟掉、只剩病名。舉例刻意不用評測題裡的題目。
        prompt = (
            "把使用者的健康問題改寫成搜尋用的查詢，不要回答問題。\n"
            "規則：\n"
            "1. 只能使用使用者訊息裡出現的疾病、症狀、食物、藥物或說法；"
            "禁止自行推論或補上病名（例如使用者只說頭暈、耳鳴，"
            "就不能寫成梅尼爾氏症）。\n"
            "2. 使用者在查證某個說法時，關鍵字必須保留該說法的主體"
            "（例如「喝醋可以軟化血管」要保留「喝醋」與「血管」）。\n"
            "3. 縮寫必須展開成全名（例如 COPD → 慢性阻塞性肺病／"
            "chronic obstructive pulmonary disease）；zh_terms 不可包含英文縮寫。\n"
            "4. 使用者用注音或語音輸入，常選到同音的錯字（例如「圓錐膠膜」其實是"
            "「圓錐角膜」）。只有在原字組起來不是常見說法、換成同音字才是常見醫學用語時，"
            "才把 typo_from 填原句裡的錯字詞、typo_to 填更正後的詞，並在 kb_query、"
            "zh_terms、en_terms 改用更正後的詞；不確定就兩者都填空字串。"
            "這是更正打錯的字，不是規則 1 禁止的補上病名：只能換同音字，"
            "不能換成讀音不同的字或別的病名。\n"
            "kb_query：一句更具體、利於向量檢索的繁體中文問句。\n"
            "zh_terms：最多三個台灣衛教常用的繁體中文關鍵字，以空白分隔。\n"
            "en_terms：對應的英文醫學關鍵字，最多三個，以空白分隔。\n"
            "typo_from／typo_to：規則 4 的錯字與更正；沒有錯字時都填空字串。\n\n"
            f"原始問題：{query}\n\n"
            "知識庫目前檢索到的片段（僅供理解問題，可能不相關）：\n"
            + ("\n".join(snippets) if snippets else "(無)")
        )
        raw = await self._call(prompt)
        if not isinstance(raw, dict):
            raw = {}
        kb_query = str(raw.get("kb_query") or "").strip() or query
        zh_terms = str(raw.get("zh_terms") or "")
        en_terms = str(raw.get("en_terms") or "")
        typo_from = str(raw.get("typo_from") or "").strip()
        typo_to = str(raw.get("typo_to") or "").strip()
        typo_fix = accept_typo_fix(query, typo_from, typo_to)
        if typo_to and typo_to != typo_from:
            log_stage(
                logger,
                "rag_typo_fix",
                outcome="accepted" if typo_fix else "rejected",
                typo_from=typo_from,
                typo_to=typo_to,
            )
        if typo_fix is None and typo_from and typo_to and typo_to != typo_from:
            # 模型照規則 4 已經把查詢裡的字換掉了，但這組更正沒通過同音檢查（例如
            # 胃痛→胃癌）：中文查詢逐字換回原字；英文那路無法逐字還原，整路不搜，
            # 寧可少一路結果，也不要拿被改過的病名去搜。
            kb_query = kb_query.replace(typo_to, typo_from)
            zh_terms = zh_terms.replace(typo_to, typo_from)
            en_terms = ""
        return RewrittenQuery(
            kb_query=kb_query,
            zh_terms=normalize_zh_terms(zh_terms),
            en_terms=normalize_en_terms(en_terms),
            typo_fix=typo_fix,
        )

    async def _call(self, prompt: str) -> dict[str, Any]:
        if self._invoke_rewrite is not None:
            return await self._invoke_rewrite(prompt)
        if self._gemini is None:
            raise RuntimeError("GeminiQueryRewriter requires gemini_service or invoke_rewrite")
        structured = self._gemini.chat_model.with_structured_output(
            REWRITE_SCHEMA,
            method="json_schema",
        )
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        if not isinstance(result, dict):
            raise ValueError(f"unexpected rewrite payload: {type(result)}")
        return result
