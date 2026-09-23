"""resolve_page_title：PDF 的標題要從內文認，不能信 metadata。"""

from app.services.rag.web_client import ScrapedPage, resolve_page_title

# 2026-09-23 實測：疾管署這份 PDF 的 metadata Title 還停在被另存前的舊檔名稱
EBOLA_PDF = ScrapedPage(
    text="# 伊波拉病毒感染 Q＆A\n\n疾病管制署 103 年 10 月 6 日修訂\n\n**Q1**：什麼是伊波拉病毒感染？",
    final_url="https://www.cdc.gov.tw/uploads/files/887a577d.pdf",
    title="中東呼吸症候群冠狀病毒感染症 Q＆A",
    content_type="application/pdf",
)


def test_pdf_prefers_first_heading_over_stale_metadata_title():
    assert resolve_page_title(EBOLA_PDF) == "伊波拉病毒感染 Q＆A"


def test_pdf_without_metadata_title_gets_title_from_content():
    """標題空白的 PDF 過去會被收錄端整份拒收（衛福部跌倒預防手冊即是）。"""
    page = ScrapedPage(
        text="# 照護及指導指引攜帶手冊\n\n## 簡介\n\n本指引旨在提高高齡者護理的安全性。",
        title="",
        content_type="application/pdf",
    )
    assert resolve_page_title(page) == "照護及指導指引攜帶手冊"


def test_pdf_falls_back_to_metadata_title_when_no_heading():
    page = ScrapedPage(
        text="這份 PDF 的內文沒有任何 markdown 標題。", title="衛教單張", content_type="application/pdf"
    )
    assert resolve_page_title(page) == "衛教單張"


def test_html_keeps_metadata_title():
    """HTML 的 <title> 可信；內文開頭常是「跳到主要內容區塊」這類導覽文字。"""
    page = ScrapedPage(
        text="# 跳到主要內容區塊\n\n## 頭痛之照護",
        title="頭痛之照護 | 臺北榮總護理部健康e點通",
        content_type="text/html",
    )
    assert resolve_page_title(page) == "頭痛之照護 | 臺北榮總護理部健康e點通"


def test_unknown_content_type_keeps_metadata_title():
    """抓取端沒回報 contentType 時維持原行為。"""
    page = ScrapedPage(text="# 內文標題", title="頁面標題")
    assert resolve_page_title(page) == "頁面標題"


def test_overlong_heading_is_not_taken_as_title():
    """一行超過 120 字的不是標題，是被當成一行的段落。"""
    page = ScrapedPage(
        text="# " + "很長的一段文字" * 30 + "\n\n# 真正的標題",
        title="備用標題",
        content_type="application/pdf",
    )
    assert resolve_page_title(page) == "真正的標題"


def test_heading_deeper_in_document_is_ignored():
    """只掃開頭 20 行：標題印在第一頁最上方。"""
    page = ScrapedPage(
        text="\n".join(["內文"] * 30) + "\n# 附錄標題",
        title="封面標題",
        content_type="application/pdf",
    )
    assert resolve_page_title(page) == "封面標題"
