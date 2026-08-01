from langchain_core.messages import ToolMessage

from app.services.agent.agent import summarize_tool_messages


def test_summarize_tool_messages_includes_preview_and_sources_flag():
    messages = [
        ToolMessage(
            content=(
                "根據 RAG 資訊，高血壓建議低鈉飲食。\n\n"
                "參考資料來源：\n[1] 國健署：https://www.hpa.gov.tw/x"
            ),
            tool_call_id="1",
            name="get_rag_answer",
        )
    ]
    summaries = summarize_tool_messages(messages, preview_len=20)
    assert len(summaries) == 1
    assert summaries[0]["name"] == "get_rag_answer"
    assert summaries[0]["has_sources"] is True
    assert summaries[0]["preview"].startswith("根據 RAG 資訊")
    assert summaries[0]["preview"].endswith("…")
    assert len(summaries[0]["preview"]) == 21


def test_summarize_tool_messages_handles_short_answer_without_sources():
    messages = [
        ToolMessage(
            content="知識庫中未找到相關資訊，請嘗試用不同方式描述問題。",
            tool_call_id="2",
            name="get_rag_answer",
        )
    ]
    summaries = summarize_tool_messages(messages)
    assert summaries[0]["has_sources"] is False
    assert "未找到" in summaries[0]["preview"]


def test_summarize_tool_messages_also_works_for_web_tool():
    messages = [
        ToolMessage(
            content="以下參考網路公開資料\n\n請規律量測血壓。\n\n參考資料來源：\n[1] 網路：疾管署：https://www.cdc.gov.tw/w",
            tool_call_id="3",
            name="search_public_web",
        )
    ]
    summaries = summarize_tool_messages(messages, preview_len=40)
    assert summaries[0]["name"] == "search_public_web"
    assert summaries[0]["has_sources"] is True
    assert "網路公開資料" in summaries[0]["preview"]
