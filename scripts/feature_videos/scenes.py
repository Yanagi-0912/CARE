"""操作示範影片的劇本：每一頁功能一段，每種語言各一份。

按鈕、卡片標題、CARE 的固定回覆都直接取 app/i18n 的文字，跟使用者在 LINE 上
看到的一致；改了那邊的文字，重跑腳本影片就跟著更新。只有 agent 生成的回答
（血壓、藥袋、回報知識）與畫面上的步驟說明寫在這裡，那些在線上本來就每次
不同，影片右上角標「示範畫面」，影片卡片的內文也講明實際回覆會依問題不同。

示範用的藥袋、院所名稱都是虛構的。藥袋保持中文：在台灣拿到的藥袋就是中文，
外語使用者看到的也是中文藥袋，CARE 再用他的語言解釋。
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from app.i18n import t

# 劇本固定的時間點都以秒為單位；FADE 與 chat.html 一致。
TYPING_SECONDS_PER_CHAR = 0.13
TYPING_MAX_SECONDS = 2.2

HTML_LANG = {"zh-TW": "zh-Hant", "en": "en", "id": "id", "vi": "vi", "th": "th", "ja": "ja"}

# 這幾個 key 的文字在 i18n 裡沒有，只有影片用得到。
MOCK: dict[str, dict[str, Any]] = {
    "zh-TW": {
        "demo_badge": "示範畫面",
        "captions": {
            "ai": ["點一下想問的問題", "CARE 會回答，並附上資料來源"],
            "media": ["按下面的「{camera}」", "對準藥袋，按下快門", "CARE 會告訴你藥名和怎麼吃"],
            "daily": ["吃藥時間到，CARE 會提醒你", "吃完按「{taken}」", "也可以直接說「早上那頓我吃過了」"],
            "resources": ["點「{nearby}」", "按「{share}」，再按「{send}」", "CARE 會列出附近的院所"],
            "family": ["點「{invite}」", "請家人掃這個 QR code，加 CARE 好友", "要讓家人加入你的家庭，按「{family}」"],
            "other": ["覺得資料過時，直接打字告訴 CARE", "CARE 會轉給團隊檢查"],
        },
        "ai_answer": "依台灣高血壓治療指引，血壓持續在 130/80 mmHg 以上，就屬於高血壓。建議每天固定時間在家量血壓並記錄下來，看診時帶給醫師參考。",
        "ai_source": "國民健康署",
        "drug_reply": "這張藥袋上有 2 種藥：\n1. Amlodipine 5 毫克：每天早上 1 顆，用來降血壓。\n2. Metformin 500 毫克：早晚飯後各 1 顆，用來控制血糖。",
        "map_title": "位置資訊",
        "map_addr": "目前位置附近",
        "send": "傳送",
        "location_label": "目前位置",
        "clinics_title": "附近的診所",
        "open_now": "看診中",
        "opens_at": "14:30 開診",
        "flu_question": "流感疫苗什麼時候可以打？",
        "flu_answer": "公費流感疫苗每年 10 月開始接種，65 歲以上長者可以免費施打。",
        "outdated": "這個資料過時了",
        "report_reply": "謝謝你的回報！我已經把這則內容轉給團隊檢查。",
    },
    "en": {
        "demo_badge": "Demo",
        "captions": {
            "ai": ["Tap a question you want to ask", "CARE answers and shows the sources"],
            "media": ["Tap “{camera}” below", "Point at the medicine bag and take the photo", "CARE tells you the drug names and how to take them"],
            "daily": ["When it's time, CARE reminds you", "After taking it, tap “{taken}”", "Or just say “I took my morning dose.”"],
            "resources": ["Tap “{nearby}”", "Tap “{share}”, then “{send}”", "CARE lists the places near you"],
            "family": ["Tap “{invite}”", "Your family scans this QR code to add CARE", "To add them to your family, tap “{family}”"],
            "other": ["If something seems outdated, just type it to CARE", "CARE passes it to the team to check"],
        },
        "ai_answer": "Under Taiwan's hypertension guidelines, blood pressure that stays at 130/80 mmHg or higher counts as high. Measure at home at the same times each day, write it down, and show your doctor.",
        "ai_source": "Health Promotion Administration, Taiwan",
        "drug_reply": "This medicine bag has 2 drugs:\n1. Amlodipine 5 mg: 1 tablet every morning, for blood pressure.\n2. Metformin 500 mg: 1 tablet after breakfast and dinner, for blood sugar.",
        "map_title": "Location",
        "map_addr": "Near your current location",
        "send": "Send",
        "location_label": "Current location",
        "clinics_title": "Clinics near you",
        "open_now": "Open now",
        "opens_at": "Opens 14:30",
        "flu_question": "When can I get a flu shot?",
        "flu_answer": "Free flu shots start every October, and people 65 and older can get one at no cost.",
        "outdated": "This information is outdated",
        "report_reply": "Thanks for letting me know! I've passed this to the team to check.",
    },
    "id": {
        "demo_badge": "Contoh",
        "captions": {
            "ai": ["Ketuk pertanyaan yang ingin ditanyakan", "CARE menjawab dan menyertakan sumbernya"],
            "media": ["Ketuk “{camera}” di bawah", "Arahkan ke kantong obat, lalu ambil foto", "CARE memberi tahu nama obat dan cara minumnya"],
            "daily": ["Saat waktunya tiba, CARE mengingatkan", "Setelah minum obat, ketuk “{taken}”", "Atau bilang saja “Obat pagi sudah saya minum.”"],
            "resources": ["Ketuk “{nearby}”", "Ketuk “{share}”, lalu “{send}”", "CARE menampilkan fasilitas kesehatan terdekat"],
            "family": ["Ketuk “{invite}”", "Keluarga memindai kode QR ini untuk menambahkan CARE", "Agar mereka masuk ke keluarga Anda, ketuk “{family}”"],
            "other": ["Kalau info terasa usang, ketik saja ke CARE", "CARE meneruskannya ke tim untuk diperiksa"],
        },
        "ai_answer": "Menurut pedoman hipertensi Taiwan, tekanan darah yang terus di angka 130/80 mmHg atau lebih dianggap tinggi. Ukur di rumah pada jam yang sama setiap hari, catat, lalu tunjukkan ke dokter.",
        "ai_source": "Health Promotion Administration, Taiwan",
        "drug_reply": "Di kantong obat ini ada 2 obat:\n1. Amlodipine 5 mg: 1 tablet setiap pagi, untuk tekanan darah.\n2. Metformin 500 mg: 1 tablet setelah sarapan dan makan malam, untuk gula darah.",
        "map_title": "Lokasi",
        "map_addr": "Di sekitar lokasi Anda",
        "send": "Kirim",
        "location_label": "Lokasi saat ini",
        "clinics_title": "Klinik terdekat",
        "open_now": "Buka",
        "opens_at": "Buka 14.30",
        "flu_question": "Kapan bisa vaksin flu?",
        "flu_answer": "Vaksin flu gratis dimulai setiap Oktober, dan lansia 65 tahun ke atas bisa mendapatkannya tanpa biaya.",
        "outdated": "Info ini sudah usang",
        "report_reply": "Terima kasih atas laporannya! Sudah saya teruskan ke tim untuk diperiksa.",
    },
    "vi": {
        "demo_badge": "Minh họa",
        "captions": {
            "ai": ["Chạm vào câu hỏi bạn muốn hỏi", "CARE trả lời và ghi rõ nguồn"],
            "media": ["Bấm “{camera}” ở dưới", "Hướng vào túi thuốc rồi chụp", "CARE cho biết tên thuốc và cách uống"],
            "daily": ["Đến giờ, CARE sẽ nhắc bạn", "Uống xong, bấm “{taken}”", "Hoặc nói “Tôi đã uống thuốc buổi sáng rồi.”"],
            "resources": ["Bấm “{nearby}”", "Bấm “{share}”, rồi bấm “{send}”", "CARE liệt kê các cơ sở y tế gần bạn"],
            "family": ["Bấm “{invite}”", "Người thân quét mã QR này để kết bạn với CARE", "Để thêm họ vào gia đình bạn, bấm “{family}”"],
            "other": ["Thấy thông tin đã cũ, cứ gõ cho CARE", "CARE sẽ chuyển cho nhóm kiểm tra"],
        },
        "ai_answer": "Theo hướng dẫn điều trị tăng huyết áp của Đài Loan, huyết áp thường xuyên từ 130/80 mmHg trở lên là cao. Hãy đo tại nhà vào cùng giờ mỗi ngày, ghi lại và đưa bác sĩ xem.",
        "ai_source": "Health Promotion Administration, Taiwan",
        "drug_reply": "Túi thuốc này có 2 loại thuốc:\n1. Amlodipine 5 mg: mỗi sáng 1 viên, để hạ huyết áp.\n2. Metformin 500 mg: 1 viên sau bữa sáng và bữa tối, để kiểm soát đường huyết.",
        "map_title": "Vị trí",
        "map_addr": "Gần vị trí hiện tại của bạn",
        "send": "Gửi",
        "location_label": "Vị trí hiện tại",
        "clinics_title": "Phòng khám gần bạn",
        "open_now": "Đang mở cửa",
        "opens_at": "Mở lúc 14:30",
        "flu_question": "Khi nào có thể tiêm vắc-xin cúm?",
        "flu_answer": "Vắc-xin cúm miễn phí bắt đầu tiêm vào tháng 10 hằng năm, người từ 65 tuổi trở lên được tiêm không mất tiền.",
        "outdated": "Thông tin này cũ rồi",
        "report_reply": "Cảm ơn bạn đã báo! Tôi đã chuyển nội dung này cho nhóm kiểm tra.",
    },
    "th": {
        "demo_badge": "ตัวอย่าง",
        "captions": {
            "ai": ["แตะคำถามที่อยากถาม", "CARE จะตอบพร้อมแหล่งที่มา"],
            "media": ["แตะ “{camera}” ด้านล่าง", "เล็งไปที่ซองยาแล้วกดถ่าย", "CARE จะบอกชื่อยาและวิธีกิน"],
            "daily": ["ถึงเวลา CARE จะเตือนคุณ", "กินเสร็จแล้วแตะ “{taken}”", "หรือพูดว่า “กินยาเช้าแล้ว”"],
            "resources": ["แตะ “{nearby}”", "แตะ “{share}” แล้วแตะ “{send}”", "CARE จะแสดงสถานพยาบาลใกล้ ๆ"],
            "family": ["แตะ “{invite}”", "ให้ครอบครัวสแกน QR code นี้เพื่อเพิ่ม CARE เป็นเพื่อน", "ถ้าจะให้เข้าร่วมครอบครัวของคุณ แตะ “{family}”"],
            "other": ["ถ้าข้อมูลดูเก่า พิมพ์บอก CARE ได้เลย", "CARE จะส่งต่อให้ทีมตรวจสอบ"],
        },
        "ai_answer": "ตามแนวทางรักษาความดันโลหิตสูงของไต้หวัน ถ้าความดันอยู่ที่ 130/80 mmHg ขึ้นไปเป็นประจำถือว่าสูง ควรวัดที่บ้านเวลาเดิมทุกวัน จดไว้ แล้วให้แพทย์ดู",
        "ai_source": "Health Promotion Administration, Taiwan",
        "drug_reply": "ซองยานี้มียา 2 ชนิด:\n1. Amlodipine 5 มก.: เช้าละ 1 เม็ด สำหรับลดความดัน\n2. Metformin 500 มก.: หลังอาหารเช้าและเย็น ครั้งละ 1 เม็ด สำหรับคุมน้ำตาลในเลือด",
        "map_title": "ตำแหน่ง",
        "map_addr": "ใกล้ตำแหน่งปัจจุบันของคุณ",
        "send": "ส่ง",
        "location_label": "ตำแหน่งปัจจุบัน",
        "clinics_title": "คลินิกใกล้คุณ",
        "open_now": "เปิดอยู่",
        "opens_at": "เปิด 14:30 น.",
        "flu_question": "ฉีดวัคซีนไข้หวัดใหญ่ได้เมื่อไหร่?",
        "flu_answer": "วัคซีนไข้หวัดใหญ่ฟรีเริ่มฉีดทุกเดือนตุลาคม ผู้สูงอายุ 65 ปีขึ้นไปฉีดได้โดยไม่เสียค่าใช้จ่าย",
        "outdated": "ข้อมูลนี้เก่าแล้ว",
        "report_reply": "ขอบคุณที่แจ้งมา! ส่งต่อให้ทีมตรวจสอบแล้ว",
    },
    "ja": {
        "demo_badge": "デモ画面",
        "captions": {
            "ai": ["聞きたい質問をタップ", "CARE が出典つきで答えます"],
            "media": ["下の「{camera}」をタップ", "お薬の袋に向けて撮影", "CARE が薬の名前と飲み方を教えます"],
            "daily": ["時間になると CARE がお知らせ", "飲んだら「{taken}」をタップ", "「朝の薬は飲みました」と送ってもOK"],
            "resources": ["「{nearby}」をタップ", "「{share}」→「{send}」をタップ", "CARE が近くの医療機関を表示します"],
            "family": ["「{invite}」をタップ", "家族にこの QR コードを読み取って CARE を友だち追加してもらいます", "家族グループに入ってもらうには「{family}」をタップ"],
            "other": ["情報が古いと思ったら、そのまま CARE に送信", "CARE がチームに確認を依頼します"],
        },
        "ai_answer": "台湾の高血圧治療ガイドラインでは、血圧が 130/80 mmHg 以上の状態が続くと高血圧とされます。毎日同じ時間に家で測って記録し、受診のときに医師に見せましょう。",
        "ai_source": "台湾 衛生福利部国民健康署",
        "drug_reply": "このお薬袋には 2 種類の薬があります：\n1. アムロジピン 5mg：毎朝 1 錠、血圧を下げる薬です。\n2. メトホルミン 500mg：朝夕の食後に 1 錠ずつ、血糖を下げる薬です。",
        "map_title": "位置情報",
        "map_addr": "現在地の近く",
        "send": "送信",
        "location_label": "現在地",
        "clinics_title": "近くのクリニック",
        "open_now": "診療中",
        "opens_at": "14:30 から診療",
        "flu_question": "インフルエンザの予防接種はいつ受けられますか？",
        "flu_answer": "公費のインフルエンザ予防接種は毎年10月に始まり、65歳以上は無料で受けられます。",
        "outdated": "この情報は古いです",
        "report_reply": "お知らせありがとうございます！チームに確認を依頼しました。",
    },
}

# 虛構的院所：名稱保持中文（線上資料本來就是中文），距離與狀態照語言。
CLINICS = (("仁心診所", "0.3 km", "open_now"), ("安康家醫科診所", "0.6 km", "open_now"), ("惠民耳鼻喉科診所", "0.9 km", "opens_at"))


@dataclass
class Scene:
    duration: float
    items: list[dict[str, Any]] = field(default_factory=list)
    captions: list[dict[str, Any]] = field(default_factory=list)
    taps: list[dict[str, Any]] = field(default_factory=list)
    overlays: list[dict[str, Any]] = field(default_factory=list)
    quick: dict[str, Any] | None = None
    typing: dict[str, Any] | None = None
    camera_html: str = ""
    map_html: str = ""
    map_title: str = ""
    map_addr: str = ""
    map_send: str = ""

    def add(self, item_id: str, html_: str, start: float, end: float | None = None) -> None:
        self.items.append({"id": item_id, "html": html_, "in": start, "out": end})

    def caption(self, text: str, start: float, end: float | None = None) -> None:
        self.captions.append({"text": text, "in": start, "out": end})

    def tap(self, target: str, at: float) -> None:
        self.taps.append({"target": target, "at": at})


def _e(text: str) -> str:
    return html.escape(text)


def _care(item_id: str, inner: str) -> str:
    return f'<div class="row care" id="{item_id}"><div class="avatar">C</div>{inner}</div>'


def _user(item_id: str, inner: str) -> str:
    return f'<div class="row user" id="{item_id}">{inner}</div>'


def _bubble(text: str, extra: str = "") -> str:
    return f'<div class="bubble">{_e(text)}{extra}</div>'


def _loading(item_id: str) -> str:
    return _care(item_id, '<div class="bubble loading"><i class="dot"></i><i class="dot"></i><i class="dot"></i></div>')


def _button(label: str, button_id: str | None = None, kind: str = "") -> str:
    id_attr = f' id="{button_id}"' if button_id else ""
    return f'<div class="fbtn {kind}"{id_attr}>{_e(label)}</div>'


def _card(head: str, body: str, *, done: bool = False) -> str:
    head_cls = "head done" if done else "head"
    return f'<div class="card"><div class="{head_cls}">{_e(head)}</div><div class="body">{body}</div></div>'


def _section_card(lang: str, section: str, example_keys: tuple[str, ...]) -> str:
    buttons = "".join(
        _button(t(key, lang), f"btn-ex-{i}") for i, key in enumerate(example_keys)
    )
    body = f'<div class="caption">{_e(t("welcome.try_hint", lang))}</div>{buttons}'
    return _card(t(f"welcome.section.{section}.title", lang), body)


def _drug_bag(scale: float = 1.0) -> str:
    # 虛構病人與診所；姓名遮成「王○○」。
    style = f' style="transform: scale({scale})"' if scale != 1.0 else ""
    return (
        f'<div class="bag"{style}>'
        '<div class="title">藥袋</div>'
        "<div>姓名：王○○　　年齡：72</div>"
        "<div>調劑日期：115/10/20</div>"
        '<div class="line"></div>'
        '<div class="drug">Amlodipine 5mg</div><div>每日早上 1 顆，飯後服用</div>'
        '<div class="line"></div>'
        '<div class="drug">Metformin 500mg</div><div>早晚飯後各 1 顆</div>'
        '<div class="clinicname">範例家醫科診所</div>'
        "</div>"
    )


def _map_tiles() -> str:
    roads = [
        ("left:0;right:0;top:38%;height:14px", ""),
        ("left:0;right:0;top:72%;height:10px", ""),
        ("top:0;bottom:0;left:30%;width:12px", ""),
        ("top:0;bottom:0;left:68%;width:16px", ""),
    ]
    parts = [f'<div class="road" style="{style}"></div>' for style, _ in roads]
    parts.append('<div class="park" style="left:6%;top:6%;width:20%;height:26%"></div>')
    parts.append('<div class="park" style="left:74%;top:78%;width:22%;height:18%"></div>')
    parts.append('<div class="pin"></div>')
    return f'<div class="map">{"".join(parts)}</div>'


def _qr() -> str:
    # 假的 QR 圖樣：三個定位框加上固定的雜點，掃不出任何東西。
    size = 21
    cells = []
    for y in range(size):
        for x in range(size):
            for ox, oy in ((0, 0), (14, 0), (0, 14)):
                lx, ly = x - ox, y - oy
                if 0 <= lx < 7 and 0 <= ly < 7:
                    on = lx in (0, 6) or ly in (0, 6) or (2 <= lx <= 4 and 2 <= ly <= 4)
                    break
            else:
                on = (x * 7 + y * 13 + x * y) % 5 < 2
            cells.append('<i class="on"></i>' if on else "<i></i>")
    return f'<div class="qr">{"".join(cells)}</div>'


def _captions(lang: str, section: str, **labels: str) -> list[str]:
    return [text.format(**labels) for text in MOCK[lang]["captions"][section]]


def build_scene(lang: str, section: str) -> dict[str, Any]:
    builder = _BUILDERS[section]
    scene = builder(lang, MOCK[lang])
    data = scene.__dict__.copy()
    data["html_lang"] = HTML_LANG[lang]
    data["demo_badge"] = MOCK[lang]["demo_badge"]
    return data


def _scene_ai(lang: str, m: dict[str, Any]) -> Scene:
    s = Scene(duration=11.0)
    caps = _captions(lang, "ai")
    s.add("card", _care("card", _section_card(lang, "ai", ("welcome.example.health", "welcome.example.rumor"))), 0)
    s.tap("btn-ex-0", 1.7)
    s.add("q", _user("q", _bubble(t("welcome.example.health", lang))), 2.1)
    s.add("wait", _loading("wait"), 2.5, 4.3)
    sources = f'<span class="sources">{_e(t("agent.sources_heading", lang))}\n{_e(m["ai_source"])}</span>'
    s.add("a", _care("a", _bubble(m["ai_answer"], sources)), 4.3)
    s.caption(caps[0], 0.3, 3.9)
    s.caption(caps[1], 4.7, None)
    return s


def _scene_media(lang: str, m: dict[str, Any]) -> Scene:
    s = Scene(duration=12.0)
    camera = t("welcome.quick.camera", lang)
    caps = _captions(lang, "media", camera=camera)
    intro = _card(t("welcome.title", lang), f'<div class="lead">{_e(t("welcome.lead", lang))}</div>')
    s.add("intro", _care("intro", intro), 0)
    camera_icon = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="#333" stroke-width="2"><rect x="3" y="7" width="18" '
        'height="13" rx="2"/><circle cx="12" cy="13.5" r="3.5"/></svg>'
    )
    roll_icon = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="#333" stroke-width="2"><rect x="3" y="4" width="18" '
        'height="16" rx="2"/><path d="M3 16l5-5 4 4 3-3 6 6"/></svg>'
    )
    s.quick = {
        "in": 0,
        "out": 2.0,
        "html": (
            f'<div class="chip" id="chip-camera">{camera_icon}{_e(camera)}</div>'
            f'<div class="chip">{roll_icon}{_e(t("welcome.quick.camera_roll", lang))}</div>'
        ),
    }
    s.tap("chip-camera", 1.5)
    s.camera_html = _drug_bag()
    s.overlays.append({"id": "camera", "in": 1.9, "out": 4.0})
    s.tap("shutter", 3.4)
    s.overlays.append({"id": "flash", "in": 3.45, "out": 3.75})
    s.add("photo", _user("photo", f'<div class="photo">{_drug_bag(0.68)}</div>'), 4.0)
    s.add("wait", _loading("wait"), 4.4, 6.2)
    s.add("a", _care("a", _bubble(m["drug_reply"])), 6.2)
    s.caption(caps[0], 0.3, 1.9)
    s.caption(caps[1], 2.2, 4.0)
    s.caption(caps[2], 6.5, None)
    return s


def _scene_daily(lang: str, m: dict[str, Any]) -> Scene:
    s = Scene(duration=11.5)
    taken = t("flex.med.button.taken", lang)
    caps = _captions(lang, "daily", taken=taken)
    drug = '<div class="pill"><span class="tablet"></span><span class="strong">Amlodipine 5mg</span></div>'
    reminder_body = (
        f'<div class="strong">{_e(t("slot.morning", lang))} 08:00</div>{drug}'
        f'<div class="caption">{_e(t("flex.med.instruction.brief", lang))}</div>'
        f'{_button(taken, "btn-taken", "primary")}'
    )
    s.add("remind", _care("remind", _card(t("flex.med.header.reminder", lang), reminder_body)), 0.3)
    s.tap("btn-taken", 4.2)
    s.add("ack", _user("ack", _bubble(t("flex.med.display.taken", lang))), 4.6)
    done_body = f'<div class="strong">{_e(t("flex.med.done_at", lang).format(time="08:05"))}</div>{drug}'
    s.add("done", _care("done", _card(t("flex.med.header.done", lang), done_body, done=True)), 5.2)
    s.caption(caps[0], 0.6, 2.9)
    s.caption(caps[1], 3.0, 4.9)
    s.caption(caps[2], 6.0, None)
    return s


def _scene_resources(lang: str, m: dict[str, Any]) -> Scene:
    s = Scene(duration=13.5)
    nearby = t("welcome.example.nearby", lang)
    share = t("location.share_qr_label", lang)
    caps = _captions(lang, "resources", nearby=nearby, share=share, send=m["send"])
    card = _section_card(lang, "resources", ("welcome.example.department", "welcome.example.nearby"))
    s.add("card", _care("card", card), 0)
    s.tap("btn-ex-1", 1.7)
    s.add("q", _user("q", _bubble(nearby)), 2.1)
    s.add("wait", _loading("wait"), 2.5, 3.7)
    s.add("ask", _care("ask", _bubble(t("location.share_prompt", lang))), 3.7)
    pin_icon = (
        '<svg viewBox="0 0 24 24" fill="#E53935"><path d="M12 2a7 7 0 0 0-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 0 0-7-7z"/>'
        '<circle cx="12" cy="9" r="2.5" fill="#fff"/></svg>'
    )
    s.quick = {"in": 3.8, "out": 5.6, "html": f'<div class="chip" id="chip-share">{pin_icon}{_e(share)}</div>'}
    s.tap("chip-share", 5.3)
    s.map_html = _map_tiles()
    s.map_title = m["map_title"]
    s.map_addr = m["map_addr"]
    s.map_send = m["send"]
    s.overlays.append({"id": "maplayer", "in": 5.6, "out": 7.2})
    s.tap("map-send", 6.9)
    location = (
        f'<div class="mapthumb"><div class="tiles">{_map_tiles()}</div>'
        f'<div class="label">{_e(m["location_label"])}</div></div>'
    )
    s.add("loc", _user("loc", location), 7.2)
    s.add("wait2", _loading("wait2"), 7.6, 9.1)
    rows = "".join(
        f'<div class="clinic"><div class="strong">{_e(name)}</div>'
        f'<div class="meta">{_e(distance)}　<span class="{"open" if status == "open_now" else ""}">'
        f"{_e(m[status])}</span></div></div>"
        + ('<div class="divider"></div>' if i < len(CLINICS) - 1 else "")
        for i, (name, distance, status) in enumerate(CLINICS)
    )
    s.add("list", _care("list", _card(m["clinics_title"], rows)), 9.1)
    s.caption(caps[0], 0.3, 3.6)
    s.caption(caps[1], 4.0, 7.2)
    s.caption(caps[2], 9.4, None)
    return s


def _scene_family(lang: str, m: dict[str, Any]) -> Scene:
    s = Scene(duration=11.0)
    invite = t("welcome.example.invite", lang)
    family = t("flex.share.family_button", lang)
    caps = _captions(lang, "family", invite=invite, family=family)
    s.add("card", _care("card", _section_card(lang, "family", ("welcome.example.invite",))), 0)
    s.tap("btn-ex-0", 1.7)
    s.add("q", _user("q", _bubble(invite)), 2.1)
    share_body = (
        f'<div class="brandmark">CARE</div>'
        f'<div class="strong">{_e(t("flex.share.title", lang))}</div>'
        f'<div class="caption">{_e(t("flex.share.desc", lang))}</div>'
        f"{_qr()}"
        f'{_button(t("flex.share.button", lang), kind="primary")}'
        '<div class="divider"></div>'
        f'<div class="caption">{_e(t("flex.share.family_prompt", lang))}</div>'
        f'{_button(family, "btn-family", "outline")}'
    )
    s.add("share", _care("share", f'<div class="card"><div class="body">{share_body}</div></div>'), 2.7)
    # 這顆會打開 LIFF 的家庭頁，影片停在按下去那一刻。
    s.tap("btn-family", 7.6)
    s.caption(caps[0], 0.3, 2.4)
    s.caption(caps[1], 3.2, 6.2)
    s.caption(caps[2], 6.4, None)
    return s


def _scene_other(lang: str, m: dict[str, Any]) -> Scene:
    typed = m["outdated"]
    typing_seconds = min(TYPING_MAX_SECONDS, len(typed) * TYPING_SECONDS_PER_CHAR)
    start = 1.6
    end = start + typing_seconds
    send_at = end + 0.5
    reply_at = send_at + 2.0
    s = Scene(duration=reply_at + 5.0)
    caps = _captions(lang, "other")
    s.add("q0", _user("q0", _bubble(m["flu_question"])), 0)
    s.add("a0", _care("a0", _bubble(m["flu_answer"])), 0)
    s.tap("field", 1.2)
    s.typing = {"text": typed, "start": start, "end": end, "send_at": send_at}
    s.tap("send", send_at)
    s.add("q", _user("q", _bubble(typed)), send_at + 0.15)
    s.add("wait", _loading("wait"), send_at + 0.5, reply_at)
    s.add("a", _care("a", _bubble(m["report_reply"])), reply_at)
    s.caption(caps[0], 0.3, send_at)
    s.caption(caps[1], reply_at + 0.2, None)
    return s


_BUILDERS = {
    "ai": _scene_ai,
    "media": _scene_media,
    "daily": _scene_daily,
    "resources": _scene_resources,
    "family": _scene_family,
    "other": _scene_other,
}
