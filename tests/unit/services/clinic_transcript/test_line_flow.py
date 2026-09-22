"""在 LINE 聊天室錄看診的流程。

守的是三件事：錄音前一定徵詢過醫師同意、看診錄音不會被當成問題丟給 agent、
一般的短語音問題不會被看診錄音攔走。
"""

from urllib.parse import parse_qs

import pytest

from app.core.config import settings
from app.i18n import t
from app.models.clinic_transcript import ClinicVisitRecord
from app.services.clinic_transcript import line_flow
from app.services.clinic_transcript.line_flow import ClinicRecordingFlow
from app.services.media.mutimedia_processor import MediaTooLargeError


class _Sessions:
    def __init__(self):
        self.rows = {}

    async def save(self, session):
        self.rows[session.recorder_id] = session

    async def get(self, recorder_id):
        return self.rows.get(recorder_id)

    async def delete(self, recorder_id):
        return self.rows.pop(recorder_id, None) is not None


class _Replier:
    def __init__(self):
        self.sent = []

    async def reply_messages(self, reply_token, user_id, messages, *, language=None):
        self.sent.append(messages[0])
        return True

    @property
    def last_text(self):
        return self.sent[-1].text

    @property
    def last_actions(self):
        quick = self.sent[-1].quick_reply
        return [parse_qs(item.action.data) for item in quick.items] if quick else []


class _Service:
    def __init__(self):
        self.started = []
        self.processed = []

    async def start(self, **kwargs):
        self.started.append(kwargs)
        return ClinicVisitRecord(id="rec1", **{k: v for k, v in kwargs.items()})

    async def process(self, record, path):
        self.processed.append((record, path))


def _flow(tmp_path, *, download=None):
    service = _Service()
    replier = _Replier()
    sessions = _Sessions()
    audio_file = tmp_path / "visit.m4a"
    audio_file.write_bytes(b"x")
    downloads = []

    def fake_download(message_id, media_type, file_name, max_bytes, mime_prefixes):
        downloads.append((message_id, media_type, max_bytes, mime_prefixes))
        return audio_file

    spawned = []

    def spawn(work):
        spawned.append(work)
        work.close()  # 背景轉錄不在這裡跑，只驗有沒有排進去

    flow = ClinicRecordingFlow(
        service_provider=lambda: service,
        replier=replier,
        sessions=sessions,
        download=download or fake_download,
        spawn=spawn,
    )
    return flow, service, replier, sessions, downloads, spawned


def _voice(flow, *, duration_ms=10_000, message_id="m1"):
    return flow.handle_audio(
        "U1", "tok", "zh-TW", message_id=message_id, is_file=False, duration_ms=duration_ms
    )


@pytest.mark.asyncio
async def test_開始時先徵詢醫師同意而且沒有預設值(tmp_path):
    flow, _, replier, sessions, _, _ = _flow(tmp_path)
    await flow.start("U1", "tok", "zh-TW", appointment_id="a1", hospital_name="臺大醫院")

    assert "先問醫師" in replier.last_text
    actions = [a["action"][0] for a in replier.last_actions]
    assert actions == [line_flow.CONSENT_ACTION, line_flow.CONSENT_ACTION, line_flow.CANCEL_ACTION]
    assert sessions.rows["U1"].consent is None


@pytest.mark.asyncio
async def test_選了同意之後下一則語音就是看診錄音(tmp_path):
    flow, service, replier, sessions, downloads, spawned = _flow(tmp_path)
    await flow.start("U1", "tok", "zh-TW", appointment_id="a1", hospital_name="臺大醫院")
    await flow.choose_consent("U1", "tok", "zh-TW", "doctor_agreed")
    assert "麥克風" in replier.last_text

    # 短短十秒也算：已經說好下一則是看診錄音。
    assert await _voice(flow, duration_ms=10_000) is True

    assert service.started[0]["consent"] == "doctor_agreed"
    assert service.started[0]["appointment_id"] == "a1"
    assert service.started[0]["hospital_name"] == "臺大醫院"
    assert downloads[0][2] == settings.CLINIC_RECORDING_MAX_BYTES
    assert len(spawned) == 1
    assert "收到了" in replier.last_text
    # 這一輪結束，下一則語音回到一般問題。
    assert "U1" not in sessions.rows


@pytest.mark.asyncio
async def test_一般的短語音不會被攔(tmp_path):
    flow, service, replier, _, _, _ = _flow(tmp_path)
    assert await _voice(flow, duration_ms=15_000) is False
    assert service.started == [] and replier.sent == []


@pytest.mark.asyncio
async def test_沒先按就傳長錄音時先問而且記住那段錄音(tmp_path):
    flow, service, replier, sessions, downloads, _ = _flow(tmp_path)
    assert await _voice(flow, duration_ms=8 * 60 * 1000, message_id="long1") is True

    assert service.started == []
    assert sessions.rows["U1"].pending_message_id == "long1"
    actions = [a["action"][0] for a in replier.last_actions]
    assert line_flow.NOT_VISIT_ACTION in actions

    await flow.choose_consent("U1", "tok", "zh-TW", "self_recap")

    assert downloads[0][0] == "long1"
    assert service.started[0]["consent"] == "self_recap"


@pytest.mark.asyncio
async def test_按了開始但還沒選同意就先錄了也要先問(tmp_path):
    flow, service, _, sessions, _, _ = _flow(tmp_path)
    await flow.start("U1", "tok", "zh-TW")
    assert await _voice(flow, duration_ms=10_000, message_id="early") is True
    assert service.started == []
    assert sessions.rows["U1"].pending_message_id == "early"


@pytest.mark.asyncio
async def test_分享進來的長音檔也會問(tmp_path):
    flow, _, _, sessions, _, _ = _flow(tmp_path)
    handled = await flow.handle_audio(
        "U1", "tok", "zh-TW", message_id="f1", is_file=True,
        file_name="新錄音.m4a", file_size=5_000_000,
    )
    assert handled is True
    assert sessions.rows["U1"].pending_file_name == "新錄音.m4a"


@pytest.mark.asyncio
async def test_不是看診錄音就清掉而且不整理(tmp_path):
    flow, service, replier, sessions, _, _ = _flow(tmp_path)
    await _voice(flow, duration_ms=8 * 60 * 1000)
    await flow.cancel("U1", "tok", "zh-TW", not_visit=True)
    assert "U1" not in sessions.rows
    assert service.started == []
    assert "短一點" in replier.last_text


@pytest.mark.asyncio
async def test_狀態過期後再選同意要請他重來(tmp_path):
    flow, service, replier, _, _, _ = _flow(tmp_path)
    await flow.choose_consent("U1", "tok", "zh-TW", "doctor_agreed")
    assert "過期" in replier.last_text
    assert service.started == []


@pytest.mark.asyncio
async def test_不認得的同意值不動作(tmp_path):
    flow, _, replier, sessions, _, _ = _flow(tmp_path)
    await flow.start("U1", "tok", "zh-TW")
    before = len(replier.sent)
    await flow.choose_consent("U1", "tok", "zh-TW", "yes")
    assert len(replier.sent) == before
    assert sessions.rows["U1"].consent is None


@pytest.mark.asyncio
async def test_檔案太大就請他分段而不建紀錄(tmp_path):
    def too_large(*_):
        raise MediaTooLargeError(50_000_000, settings.CLINIC_RECORDING_MAX_BYTES)

    flow, service, replier, _, _, _ = _flow(tmp_path, download=too_large)
    await flow.start("U1", "tok", "zh-TW")
    await flow.choose_consent("U1", "tok", "zh-TW", "doctor_agreed")
    await _voice(flow)
    assert service.started == []
    assert "太大" in replier.last_text


@pytest.mark.asyncio
async def test_音檔以寬鬆的_MIME_下載(tmp_path):
    """分享的 m4a 可能被標成 video/mp4 或 application/octet-stream。"""
    flow, _, _, _, downloads, _ = _flow(tmp_path)
    await flow.start("U1", "tok", "zh-TW")
    await flow.choose_consent("U1", "tok", "zh-TW", "doctor_agreed")
    await flow.handle_audio(
        "U1", "tok", "zh-TW", message_id="f1", is_file=True, file_name="a.m4a", file_size=10
    )
    assert downloads[0][1] == "file"
    assert "video/" in downloads[0][3]


def test_postback_只帶有值的參數():
    assert parse_qs(line_flow.postback_data("x", a="1", b="")) == {"action": ["x"], "a": ["1"]}


def test_快速回覆按鈕字不超過_LINE_上限():
    for language in ("zh-TW", "en", "id", "vi", "th", "ja"):
        for key in ("doctor_agreed", "self_recap", "cancel", "not_visit"):
            assert len(t(f"clinic.chat.qr.{key}", language)) <= 20
