"""兩份逐字稿選一份：使用者這一句到底是講台語還是華語。

為什麼不先判語言再辨識：判語言最可靠的方式就是「聽聽看哪一邊聽得懂」。先做語言
判斷（LID）再辨識是串接的，等待時間相加；而且 LID 判錯一次整段逐字稿就毀了，沒有
補救。改成兩個 STT 平行送、回來再選，等待時間是兩者取大的那個。

Taigi 的多語端點（transcribe_multi_stt_billing）幫不上忙：它的語言清單裡沒有台語
（文件 6.1 節），底層 Whisper 聽到台語只會標成 zh 或 yue，再用華語的字硬寫出來。

主判準是兩份逐字稿像不像，不是台語用字：
- 講台語時 Gemini 幾乎都聽成別的話（2026-09-19 實測 24 句台語只有 1 句聽出大意，
  「後禮拜的門診敢會使改時間？」聽成「百盟金卡沒塞開時間」），兩份差很多。
- 講華語時兩邊聽到的是同一句話，台語模型只是把它寫成台語用字（「我忘記早上有沒有
  吃藥」寫成「我忘記早上有無食藥」），兩份很像。

台語用字只用來在邊界上偏袒台語。一開始只用用字表，實測 12 句台語只抓到 5 句：台語
模型寫的是「敢會使」「規暝」「跤手」「懸」這些沒收進表裡的詞，而表要收全等於重寫一
本辭典；反過來「食藥」這種收進表裡的詞，台語模型聽華語時也會寫出來，照樣誤判。

實測（2026-09-19，兩批各 24 段合成語音，台語用 Taigi TTS 念台語漢字、華語用 edge-tts
念華語，都轉成同規格 mp3）：
- 第一批用來切門檻：台語 12/12、華語 11/12。
- 第二批是沒看過的句子、不調任何參數：台語 11/12、華語 11/12。
兩批加起來 45/48。都不是真人錄音，真人講話可能落在別的地方。

判對語言不等於逐字稿是對的：同一批裡台語模型把「奶奶的血壓計怎麼用我不會」聽成
「尪仔的血佮雞按怎閒我袂曉」，語言判對了，送進 agent 的仍是壞句子。

誤判的代價不對稱，所以邊界偏向華語。兩批各有一次誤判，剛好各是一種：
- 華語被當成台語（48 段裡 2 段）→ 逐字稿換成台語模型聽到的版本，內容可能整個跑掉
  （「我頭暈想吐，要叫救護車嗎？」變成「我頭暈siáng-thuh，猶照石滬車嘛」），agent
  會答錯。這是要壓低的那一種。
- 台語被當成華語（48 段裡 1 段）→ 選到的是 Gemini 那份，那次它剛好聽出大意
  （「我頭眩想欲吐，敢愛叫救護車？」→「我頭暈想嘔吐。趕快救救我車。」），使用者
  仍被聽懂，只是回覆用華語念。
"""

from __future__ import annotations

import difflib
import re

from app.core.user_language import DEFAULT_USER_LANGUAGE, TAIWANESE_LANGUAGE

# 兩份逐字稿的相似度低於這個就判台語。2026-09-19 那批的分界：台語最高 0.60、華語最低
# 0.36，扣掉有台語用字的那兩筆之後，台語落在 0.00～0.42、華語落在 0.53～0.91。
SIMILAR_ENOUGH = 0.50
# 台語那份有台語用字時放寬到這裡：同一批裡台語有用字的最高 0.60、華語有用字的最低
# 0.67（「我忘記早上有無食藥」那句，兩邊其實聽到同一句華語）。
SIMILAR_ENOUGH_WITH_TAIGI_WORDS = 0.65

_PUNCTUATION = re.compile(r"[\s，。？！、,.?!;:；：~～「」『』（）()]+")

# 單字出現就算數：這些字在台灣華語的書面幾乎不用。
_TAIGI_CHARS = frozenset("毋袂佮蹛媠囥佗")

# 要整個詞出現才算數：組成的字單獨看會出現在華語（濟→經濟、工→工作、爿→少見但
# 成對出現才有意義），湊成詞才有鑑別度。
_TAIGI_WORDS = (
    "啥物",
    "啥人",
    "按呢",
    "按怎",
    "哪會",
    "敢有",
    "囡仔",
    "歹勢",
    "有影",
    "逐工",
    "逐家",
    "規工",
    "這馬",
    "今仔日",
    "中晝",
    "下晡",
    "半暝",
    "正爿",
    "倒爿",
    "厝內",
    "真濟",
    "足濟",
    "袂記",
    "看覓",
    "食飯",
    "食藥",
    "食飽",
    "腹肚",
)


def looks_like_taiwanese(text: str) -> bool:
    """這段逐字稿是不是台語漢字。"""
    if not text:
        return False
    if any(ch in _TAIGI_CHARS for ch in text):
        return True
    return any(word in text for word in _TAIGI_WORDS)


def _similarity(a: str, b: str) -> float:
    """兩份逐字稿有多像。空白與標點不算——Gemini 常在字與字之間插空格。"""
    return difflib.SequenceMatcher(
        None, _PUNCTUATION.sub("", a), _PUNCTUATION.sub("", b)
    ).ratio()


def choose_transcript(
    taigi_text: str | None, mandarin_text: str | None
) -> tuple[str | None, str]:
    """回傳 (要用的逐字稿, 這一句的語音語言)。

    兩邊都可能是 None（該路失敗）。兩邊都 None 時回 (None, 預設語言)，由呼叫端
    決定要不要再退回 n8n／faster-whisper。
    """
    if taigi_text and mandarin_text:
        limit = (
            SIMILAR_ENOUGH_WITH_TAIGI_WORDS
            if looks_like_taiwanese(taigi_text)
            else SIMILAR_ENOUGH
        )
        if _similarity(taigi_text, mandarin_text) < limit:
            return taigi_text, TAIWANESE_LANGUAGE
        return mandarin_text, DEFAULT_USER_LANGUAGE
    # 只剩一份時沒得比，只能看台語用字。
    if taigi_text and looks_like_taiwanese(taigi_text):
        return taigi_text, TAIWANESE_LANGUAGE
    if mandarin_text:
        return mandarin_text, DEFAULT_USER_LANGUAGE
    # 只剩台語那份、而且它沒有台語用字：台語專用模型聽華語會寫出亂字（那句
    # 「野野，離近仔日，鐵藥了無有」），拿去當逐字稿等於把使用者的問題換成另一
    # 句話送進 agent。寧可回 None 讓呼叫端退 n8n／faster-whisper。
    return None, DEFAULT_USER_LANGUAGE
