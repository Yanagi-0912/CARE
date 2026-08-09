"""藥證庫比對。

存在的理由只有一個：偵測視覺模型的錯讀。模型把「脈優錠」讀成形近的其他藥名時，
它自述的信心度仍然很高，唯一能發現該字串不對應任何一張核准藥證的方法，
就是拿去跟外部字典比對。比對不到即降為低信心，強制人工核對。

建構子接受已載入的條目而非路徑，測試才能直接餵小型固定資料集，
不必碰檔案系統，也不必 monkey patch。
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# 藥證品名常見的廠商前綴：'"福元"蘇打錠500毫克'。藥袋上通常只印藥名，
# 不會帶這段，所以正規化時要拿掉。全形與半形引號都要涵蓋。
_LEADING_MANUFACTURER = re.compile(r'^\s*[「『"“”‘’](.*?)[」』"“”‘’]\s*')
_QUOTE_CHARS = re.compile(r'[「」『』"“”‘’\']')
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class DrugCatalogEntry:
    license_number: str
    name_zh: str
    name_en: str = ""


@dataclass(frozen=True)
class DrugCatalogMatch:
    license_number: str
    name_zh: str
    name_en: str
    score: float


def normalize_drug_name(name: str) -> str:
    """把藥名收斂成可比對的鍵。

    全形轉半形、去引號與廠商前綴、去除空白、統一大小寫。這些差異在藥袋與
    藥證之間普遍存在，不正規化會讓大量正確的藥名比對不到。
    """
    if not name:
        return ""
    # NFKC 一併處理全形英數與全形空白
    normalized = unicodedata.normalize("NFKC", name)
    normalized = _LEADING_MANUFACTURER.sub("", normalized)
    normalized = _QUOTE_CHARS.sub("", normalized)
    normalized = _WHITESPACE.sub("", normalized)
    return normalized.upper()


class DrugCatalogService:
    def __init__(self, entries: Iterable[DrugCatalogEntry], threshold: float):
        self._entries = list(entries)
        self._threshold = threshold
        # 正規化後的鍵 → 條目。同一條目的中英文品名各佔一個鍵。
        self._by_key: dict[str, DrugCatalogEntry] = {}
        for entry in self._entries:
            for raw_name in (entry.name_zh, entry.name_en):
                key = normalize_drug_name(raw_name)
                if key:
                    self._by_key.setdefault(key, entry)

    @property
    def is_empty(self) -> bool:
        return not self._entries

    @classmethod
    def load_from_path(cls, path: str, threshold: float) -> "DrugCatalogService":
        """從靜態檔載入。

        檔案缺席或損毀時回傳空的服務而非拋錯：藥證庫不是啟動的必要條件，
        缺席的後果是所有藥名降為低信心（每份草稿都要人工核對），
        這個退化方向是安全的，讓應用起不來則不是。
        """
        try:
            with open(path, "r", encoding="utf-8") as catalog_file:
                raw_entries = json.load(catalog_file)
            entries = [
                DrugCatalogEntry(
                    license_number=item.get("license_number", ""),
                    name_zh=item.get("name_zh", ""),
                    name_en=item.get("name_en", ""),
                )
                for item in raw_entries
            ]
            service = cls(entries, threshold=threshold)
            # 檔案存在、也成功解析成 JSON，不代表內容真的可用——欄位名稱對不上
            # FDA 資料集（例如改版換了欄位名）會讓每個 item.get(...) 都拿到
            # 空字串，最終得到一個「載入成功」但條目數是 0 或每筆都是空殼的
            # DrugCatalogService，之後所有藥名都悄悄降為低信心，卻沒有任何
            # 錯誤訊息能讓人發現問題出在資料，而不是模型辨識不準。
            if service.is_empty:
                logger.warning(
                    "藥證庫載入完成但條目數為 0，所有藥名將降為低信心並強制人工核對："
                    "%s；請確認 FDA 資料集欄位名稱與載入邏輯是否對得上",
                    path,
                )
            else:
                logger.info("藥證庫載入完成：%s，共 %d 筆條目", path, len(entries))
            return service
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.error(
                "藥證庫載入失敗，所有藥名將降為低信心並強制人工核對：%s（%s）",
                path,
                exc,
            )
            return cls([], threshold=threshold)

    def match(self, name: str) -> Optional[DrugCatalogMatch]:
        key = normalize_drug_name(name)
        if not key or not self._by_key:
            return None

        exact = self._by_key.get(key)
        if exact is not None:
            return self._to_match(exact, 1.0)

        best_entry: Optional[DrugCatalogEntry] = None
        best_score = 0.0
        for candidate_key, entry in self._by_key.items():
            score = SequenceMatcher(None, key, candidate_key).ratio()
            if score > best_score:
                best_entry, best_score = entry, score

        if best_entry is None or best_score < self._threshold:
            return None
        return self._to_match(best_entry, best_score)

    @staticmethod
    def _to_match(entry: DrugCatalogEntry, score: float) -> DrugCatalogMatch:
        return DrugCatalogMatch(
            license_number=entry.license_number,
            name_zh=entry.name_zh,
            name_en=entry.name_en,
            score=score,
        )
