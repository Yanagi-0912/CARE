import pytest

from app.core.user_language import reset_request_language, set_request_language
from app.i18n.messages import t
from app.tools import medical_tools


@pytest.mark.asyncio
async def test_request_location_quick_reply_uses_context_language():
    token = set_request_language("en")
    try:
        medical_tools.configure_medical_tools(medical_tools._medical_service)
        result = await medical_tools.request_location_quick_reply.ainvoke({})
    finally:
        reset_request_language(token)

    assert result == t("location.share_prompt", "en")
