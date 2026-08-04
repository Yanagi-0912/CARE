"""Language-aware RAG / web answer prompt builders."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from app.core.user_language import get_request_language, normalize_user_language

_LANGUAGE_NAMES: dict[str, str] = {
    "zh-TW": "Traditional Chinese (繁體中文)",
    "en": "English",
    "id": "Bahasa Indonesia",
    "vi": "Tiếng Việt",
    "th": "ภาษาไทย",
    "ja": "日本語",
}


def language_name(language: str | None = None) -> str:
    lang = normalize_user_language(language or get_request_language())
    return _LANGUAGE_NAMES[lang]


def build_rag_prompt(language: str | None = None) -> ChatPromptTemplate:
    lang_name = language_name(language)
    return ChatPromptTemplate.from_messages(
        [
            (
                "human",
                "請根據以下提供的醫療知識內容回答問題。\n\n"
                "規則：\n"
                f"0. 你必須使用{lang_name}撰寫整段回答（含說明與引用句），"
                "即使參考內容是其他語言也要翻譯／改寫成該語言；勿夾雜其他語言。"
                "專有名詞與網址可保留原文。\n"
                "1. 請在回答中適當引用內容來源的編號，例如：『...這是常見的症狀 [1]。』\n"
                "2. 回覆中不要使用「根據檢索內容」這類字眼，改用「根據 RAG 資訊」等說法"
                f"（該說法也須使用{lang_name}）。\n"
                "3. 請使用一般純文字，不要使用 Markdown 格式符號。\n"
                "4. 若內容不足，請明確說明不知道，勿捏造。\n\n"
                "使用者問題：{question}\n\n"
                "RAG 內容：\n"
                "{context}",
            )
        ]
    )


def build_user_document_prompt(language: str | None = None) -> ChatPromptTemplate:
    lang_name = language_name(language)
    return ChatPromptTemplate.from_messages(
        [
            (
                "human",
                "請根據以下使用者上傳的文件內容回答問題。\n\n"
                "規則：\n"
                f"0. 你必須使用{lang_name}撰寫整段回答。\n"
                "1. 請在回答中適當引用內容來源的編號，例如：『...如上傳文件所述 [1]。』\n"
                "2. 請使用一般純文字，不要使用 Markdown 格式符號。\n"
                "3. 若內容不足，請明確說明不知道，勿捏造。\n\n"
                "使用者問題：{question}\n\n"
                "上傳文件內容：\n"
                "{context}",
            )
        ]
    )


def build_web_prompt(language: str | None = None) -> ChatPromptTemplate:
    lang_name = language_name(language)
    return ChatPromptTemplate.from_messages(
        [
            (
                "human",
                "請根據以下提供的醫療知識內容回答問題。\n\n"
                "規則：\n"
                f"0. 你必須使用{lang_name}撰寫整段回答（含說明與引用句），"
                "即使參考內容是其他語言也要翻譯／改寫成該語言；勿夾雜其他語言。"
                "專有名詞與網址可保留原文。\n"
                "1. 請在回答中適當引用內容來源的編號，例如：『...這是常見的症狀 [1]。』\n"
                "2. 回覆中不要使用「根據檢索內容」這類字眼，改用「根據公開網路資料」等說法"
                f"（該說法也須使用{lang_name}）。\n"
                "3. 請使用一般純文字，不要使用 Markdown 格式符號。\n"
                "4. 若內容不足，請明確說明不知道，勿捏造。\n\n"
                "使用者問題：{question}\n\n"
                "網路內容：\n"
                "{context}",
            )
        ]
    )
