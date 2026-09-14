"""Localized user-facing message catalog."""

from __future__ import annotations

from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    SUPPORTED_LANGUAGES,
    get_request_language,
    normalize_user_language,
)

_MESSAGES: dict[str, dict[str, str]] = {
    # --- 緊急狀況家人通報卡 ----------------------------------------------
    #
    # 收件人是家屬，不是當事人。措辭要能讓人立刻做一件事（打電話），
    # 而不是先讀完一段說明。
    "emergency_family.alt_text": {
        "zh-TW": "{name} 可能需要立即協助",
        "en": "{name} may need immediate help",
        "id": "{name} mungkin butuh bantuan segera",
        "vi": "{name} có thể cần trợ giúp ngay",
        "th": "{name} อาจต้องการความช่วยเหลือทันที",
        "ja": "{name} さんに今すぐ助けが必要かもしれません",
    },
    "emergency_family.title": {
        "zh-TW": "家人可能需要立即協助",
        "en": "A family member may need help now",
        "id": "Anggota keluarga mungkin butuh bantuan sekarang",
        "vi": "Người thân có thể cần trợ giúp ngay",
        "th": "สมาชิกในครอบครัวอาจต้องการความช่วยเหลือตอนนี้",
        "ja": "ご家族に今すぐ助けが必要かもしれません",
    },
    "emergency_family.lead": {
        "zh-TW": "{name} 剛才在 CARE 描述的狀況，系統判定可能需要立即處置。",
        "en": (
            "What {name} just described in CARE looks like it may need "
            "immediate care."
        ),
        "id": (
            "Apa yang baru saja {name} sampaikan di CARE tampaknya "
            "memerlukan penanganan segera."
        ),
        "vi": (
            "Điều {name} vừa mô tả trong CARE có thể cần được xử trí ngay."
        ),
        "th": "สิ่งที่ {name} เพิ่งบอกใน CARE อาจต้องได้รับการดูแลทันที",
        "ja": "{name} さんが CARE で伝えた内容は、すぐの対応が必要かもしれません。",
    },
    "emergency_family.words_label": {
        "zh-TW": "{name} 剛才說的話",
        "en": "What {name} just said",
        "id": "Yang baru saja dikatakan {name}",
        "vi": "{name} vừa nói",
        "th": "สิ่งที่ {name} เพิ่งพูด",
        "ja": "{name} さんが今言ったこと",
    },
    "emergency_family.reason_label": {
        "zh-TW": "系統為什麼判定為緊急",
        "en": "Why the system flagged this",
        "id": "Alasan peringatan ini",
        "vi": "Lý do cảnh báo",
        "th": "เหตุผลที่แจ้งเตือน",
        "ja": "判定の理由",
    },
    # 急迫度判斷沒有給出白話說明時（本地模型判定、或 LLM 回了空字串）的理由。
    # 不能留空：text 元件是空字串時 LINE 會以 400 拒收整則訊息（verdict_flex.py
    # 的 _BLANK_*_FALLBACK 已因此踩過）。
    "emergency_family.default_reason": {
        "zh-TW": "對話內容顯示可能正在發生需要立即處置的狀況",
        "en": "The conversation suggests something may need immediate care right now",
        "id": "Percakapan menunjukkan mungkin ada kondisi yang perlu penanganan segera",
        "vi": "Nội dung trò chuyện cho thấy có thể đang có tình trạng cần xử trí ngay",
        "th": "บทสนทนาบ่งชี้ว่าอาจมีเหตุการณ์ที่ต้องได้รับการดูแลทันที",
        "ja": "会話の内容から、今すぐ処置が必要な状況の可能性があります",
    },
    "emergency_family.action_label": {
        "zh-TW": "現在可以做的事",
        "en": "What you can do now",
        "id": "Yang bisa Anda lakukan sekarang",
        "vi": "Việc bạn có thể làm ngay",
        "th": "สิ่งที่คุณทำได้ตอนนี้",
        "ja": "今できること",
    },
    "emergency_family.step.1": {
        "zh-TW": "先打電話給 {name}，確認他現在的狀況。",
        "en": "Call {name} first and check how they are right now.",
        "id": "Hubungi {name} lebih dulu dan pastikan keadaannya sekarang.",
        "vi": "Hãy gọi cho {name} trước để xem hiện giờ họ thế nào.",
        "th": "โทรหา {name} ก่อน เพื่อดูว่าตอนนี้เป็นอย่างไร",
        "ja": "まず {name} さんに電話して、今の様子を確かめてください。",
    },
    "emergency_family.step.2": {
        "zh-TW": "聯絡不上、或情況危急時，直接撥 119 並前往他所在的位置。",
        "en": (
            "If you cannot reach them, or it sounds serious, call 119 and go "
            "to where they are."
        ),
        "id": (
            "Jika tidak bisa dihubungi atau terdengar serius, hubungi 119 dan "
            "datangi lokasinya."
        ),
        "vi": (
            "Nếu không liên lạc được hoặc tình hình nghiêm trọng, hãy gọi 119 "
            "và đến chỗ họ."
        ),
        "th": "หากติดต่อไม่ได้หรือดูรุนแรง ให้โทร 119 และไปหาเขา",
        "ja": "連絡がつかない、または深刻な場合は 119 に通報し、その場所へ向かってください。",
    },
    "emergency_family.call_patient": {
        "zh-TW": "打電話給 {name}",
        "en": "Call {name}",
        "id": "Hubungi {name}",
        "vi": "Gọi cho {name}",
        "th": "โทรหา {name}",
        "ja": "{name} さんに電話",
    },
    "emergency_family.open_chat": {
        "zh-TW": "在 CARE 傳訊息給他",
        "en": "Message them in CARE",
        "id": "Kirim pesan lewat CARE",
        "vi": "Nhắn tin trong CARE",
        "th": "ส่งข้อความใน CARE",
        "ja": "CARE でメッセージを送る",
    },
    "emergency_family.footer": {
        "zh-TW": "這是系統依對話內容做的判斷，不是醫療診斷，也可能判斷錯誤。請以你實際聯繫到的情況為準。",
        "en": (
            "This is an automated judgement from the conversation, not a "
            "medical diagnosis, and it can be wrong. Trust what you find when "
            "you reach them."
        ),
        "id": (
            "Ini penilaian otomatis dari percakapan, bukan diagnosis medis, "
            "dan bisa saja keliru. Percayai apa yang Anda temukan saat "
            "menghubunginya."
        ),
        "vi": (
            "Đây là đánh giá tự động từ cuộc trò chuyện, không phải chẩn đoán "
            "y khoa và có thể sai. Hãy tin vào những gì bạn thấy khi liên hệ "
            "được với họ."
        ),
        "th": (
            "นี่คือการประเมินอัตโนมัติจากบทสนทนา ไม่ใช่การวินิจฉัยทางการแพทย์ "
            "และอาจผิดพลาดได้ โปรดยึดตามสิ่งที่คุณพบเมื่อติดต่อได้"
        ),
        "ja": (
            "これは会話内容からの自動判定であり、医学的診断ではなく、"
            "誤ることもあります。実際に連絡して確かめた状況を優先してください。"
        ),
    },
    "emergency_family.fallback_name": {
        "zh-TW": "你的家人",
        "en": "Your family member",
        "id": "Anggota keluarga Anda",
        "vi": "Người thân của bạn",
        "th": "สมาชิกในครอบครัวของคุณ",
        "ja": "ご家族",
    },
    # 通知當事人「家人已經知道了」。措辭刻意是支持性的而非警告式的——
    # 這則訊息的收件人正處於危機中，讀起來必須像有人來陪，不是像被舉報。
    "text.emergency.family_notified": {
        "zh-TW": "我已經讓你的家人知道你現在需要有人陪。你不用一個人撐著。",
        "en": (
            "I've let your family know you need someone with you right now. "
            "You don't have to get through this alone."
        ),
        "id": (
            "Saya sudah memberi tahu keluarga Anda bahwa Anda butuh seseorang "
            "di dekat Anda sekarang. Anda tidak perlu menghadapinya sendiri."
        ),
        "vi": (
            "Tôi đã báo cho người thân biết rằng bạn đang cần ai đó ở bên. "
            "Bạn không phải một mình vượt qua chuyện này."
        ),
        "th": (
            "ฉันได้แจ้งครอบครัวของคุณแล้วว่าตอนนี้คุณต้องการใครสักคนอยู่ด้วย "
            "คุณไม่ต้องผ่านเรื่องนี้คนเดียว"
        ),
        "ja": (
            "今そばに誰かが必要だということを、ご家族に伝えました。"
            "ひとりで抱えなくて大丈夫です。"
        ),
    },
    # --- 緊急狀況卡片 ---------------------------------------------------
    #
    # 這張卡是急救指示，SHALL 全部隨使用者語言切換。混語言比全中文更糟：
    # 副標（急迫度判斷器產生的 display）本來就會跟著語言走，若其餘文案是中文，
    # 使用者會以為系統支援他的語言，卻看不懂最關鍵的行動指示。
    "emergency.alt_text": {
        "zh-TW": "請立即就醫",
        "en": "Seek emergency care now",
        "id": "Segera cari pertolongan medis",
        "vi": "Hãy đi cấp cứu ngay",
        "th": "โปรดไปพบแพทย์ทันที",
        "ja": "すぐに受診してください",
    },
    "emergency.headline": {
        "zh-TW": "請立即就醫",
        "en": "Seek emergency care now",
        "id": "Segera cari pertolongan medis",
        "vi": "Hãy đi cấp cứu ngay",
        "th": "โปรดไปพบแพทย์ทันที",
        "ja": "すぐに受診してください",
    },
    "emergency.default_display": {
        "zh-TW": "你描述的狀況可能需要立即處置",
        "en": "What you described may need immediate care",
        "id": "Kondisi yang Anda sebutkan mungkin perlu penanganan segera",
        "vi": "Tình trạng bạn mô tả có thể cần xử trí ngay",
        "th": "อาการที่คุณอธิบายอาจต้องได้รับการรักษาทันที",
        "ja": "お話しの状況はすぐの処置が必要かもしれません",
    },
    "emergency.body.1": {
        "zh-TW": "你描述的狀況可能需要緊急處置，不建議等待一般門診掛號。",
        "en": (
            "What you described may need emergency care. "
            "Do not wait for a regular outpatient appointment."
        ),
        "id": (
            "Kondisi yang Anda sebutkan mungkin memerlukan penanganan darurat. "
            "Jangan menunggu jadwal rawat jalan biasa."
        ),
        "vi": (
            "Tình trạng bạn mô tả có thể cần cấp cứu. "
            "Không nên chờ đặt lịch khám ngoại trú thông thường."
        ),
        "th": (
            "อาการที่คุณอธิบายอาจต้องได้รับการรักษาฉุกเฉิน "
            "ไม่ควรรอคิวตรวจผู้ป่วยนอกตามปกติ"
        ),
        "ja": (
            "お話しの状況は緊急の処置が必要な可能性があります。"
            "通常の外来予約を待たないでください。"
        ),
    },
    "emergency.body.2": {
        "zh-TW": "請儘快前往最近的急診，或撥打 119 請求協助。",
        "en": "Go to the nearest emergency room as soon as possible, or call 119 for help.",
        "id": (
            "Segera pergi ke unit gawat darurat terdekat, "
            "atau hubungi 119 untuk meminta bantuan."
        ),
        "vi": "Hãy đến phòng cấp cứu gần nhất càng sớm càng tốt, hoặc gọi 119 để được trợ giúp.",
        "th": "โปรดไปห้องฉุกเฉินที่ใกล้ที่สุดโดยเร็วที่สุด หรือโทร 119 เพื่อขอความช่วยเหลือ",
        "ja": "できるだけ早く最寄りの救急外来へ行くか、119 に電話して助けを求めてください。",
    },
    "emergency.body.3": {
        "zh-TW": "若身邊有人，請讓對方陪同前往。",
        "en": "If someone is with you, ask them to go with you.",
        "id": "Jika ada orang di dekat Anda, mintalah mereka menemani Anda.",
        "vi": "Nếu có người bên cạnh, hãy nhờ họ đi cùng bạn.",
        "th": "หากมีคนอยู่ด้วย โปรดขอให้เขาไปเป็นเพื่อน",
        "ja": "そばに誰かいる場合は、付き添ってもらってください。",
    },
    "emergency.hotline_label": {
        "zh-TW": "可以馬上撥打",
        "en": "Call now",
        "id": "Bisa langsung dihubungi",
        "vi": "Có thể gọi ngay",
        "th": "โทรได้ทันที",
        "ja": "すぐに電話できます",
    },
    "emergency.call_button": {
        "zh-TW": "撥打 {name} {number}",
        "en": "Call {name} {number}",
        "id": "Hubungi {name} {number}",
        "vi": "Gọi {name} {number}",
        "th": "โทร {name} {number}",
        "ja": "{name} {number} に電話",
    },
    "emergency.footer": {
        "zh-TW": "本訊息不是醫療診斷。情況緊急時請以撥打 119 或前往急診為優先。",
        "en": (
            "This message is not a medical diagnosis. In an emergency, "
            "calling 119 or going to the emergency room comes first."
        ),
        "id": (
            "Pesan ini bukan diagnosis medis. Dalam keadaan darurat, "
            "utamakan menghubungi 119 atau pergi ke unit gawat darurat."
        ),
        "vi": (
            "Tin nhắn này không phải là chẩn đoán y khoa. Khi khẩn cấp, "
            "hãy ưu tiên gọi 119 hoặc đến phòng cấp cứu."
        ),
        "th": (
            "ข้อความนี้ไม่ใช่การวินิจฉัยทางการแพทย์ ในกรณีฉุกเฉิน "
            "ให้โทร 119 หรือไปห้องฉุกเฉินก่อนเป็นอันดับแรก"
        ),
        "ja": (
            "このメッセージは医学的診断ではありません。緊急時は "
            "119 への通報または救急外来の受診を最優先してください。"
        ),
    },
    # 專線名稱。號碼是台灣的固定值，不翻譯；名稱要讓使用者知道打過去是什麼單位。
    "emergency.hotline.119": {
        "zh-TW": "緊急救護",
        "en": "Emergency Medical Services",
        "id": "Layanan Gawat Darurat",
        "vi": "Cấp cứu y tế",
        "th": "หน่วยแพทย์ฉุกเฉิน",
        "ja": "救急",
    },
    "emergency.hotline.119.note": {
        "zh-TW": "救護車與消防",
        "en": "Ambulance and fire service",
        "id": "Ambulans dan pemadam kebakaran",
        "vi": "Xe cứu thương và cứu hỏa",
        "th": "รถพยาบาลและดับเพลิง",
        "ja": "救急車・消防",
    },
    "emergency.hotline.110": {
        "zh-TW": "警察報案",
        "en": "Police",
        "id": "Polisi",
        "vi": "Cảnh sát",
        "th": "ตำรวจ",
        "ja": "警察",
    },
    "emergency.hotline.110.note": {
        "zh-TW": "意外或人身安全",
        "en": "Accidents or personal safety",
        "id": "Kecelakaan atau keselamatan pribadi",
        "vi": "Tai nạn hoặc an toàn cá nhân",
        "th": "อุบัติเหตุหรือความปลอดภัยส่วนบุคคล",
        "ja": "事故・身の安全",
    },
    "rag.fail.KB_EMPTY": {
        "zh-TW": "知識庫目前沒有與此問題相符的資料。請換個方式描述，或必要時就醫。",
        "en": (
            "The knowledge base has no information matching this question. "
            "Please rephrase your question, or seek medical care if needed."
        ),
        "id": (
            "Basis pengetahuan saat ini tidak memiliki informasi yang sesuai dengan pertanyaan ini. "
            "Silakan ubah cara Anda bertanya, atau konsultasikan ke tenaga medis jika perlu."
        ),
        "vi": (
            "Hiện tại cơ sở tri thức không có dữ liệu phù hợp với câu hỏi này. "
            "Vui lòng diễn đạt lại, hoặc đi khám nếu cần."
        ),
        "th": (
            "ขณะนี้ฐานความรู้ไม่มีข้อมูลที่ตรงกับคำถามนี้ "
            "กรุณาเปลี่ยนวิธีถาม หรือไปพบแพทย์หากจำเป็น"
        ),
        "ja": (
            "ナレッジベースにこの質問に一致する情報がありません。"
            "別の言い方で質問するか、必要であれば医療機関を受診してください。"
        ),
    },
    "rag.fail.WEB_EMPTY": {
        "zh-TW": "知識庫與官方網站目前都找不到相符說明。請換個方式描述，或必要時就醫。",
        "en": (
            "No matching information was found in the knowledge base or official websites. "
            "Please rephrase your question, or seek medical care if needed."
        ),
        "id": (
            "Tidak ditemukan informasi yang sesuai di basis pengetahuan maupun situs resmi. "
            "Silakan ubah cara Anda bertanya, atau konsultasikan ke tenaga medis jika perlu."
        ),
        "vi": (
            "Không tìm thấy thông tin phù hợp trong cơ sở tri thức hoặc trang web chính thức. "
            "Vui lòng diễn đạt lại, hoặc đi khám nếu cần."
        ),
        "th": (
            "ไม่พบข้อมูลที่ตรงกันทั้งในฐานความรู้และเว็บไซต์ทางการ "
            "กรุณาเปลี่ยนวิธีถาม หรือไปพบแพทย์หากจำเป็น"
        ),
        "ja": (
            "ナレッジベースと公式サイトのいずれにも一致する情報が見つかりませんでした。"
            "別の言い方で質問するか、必要であれば医療機関を受診してください。"
        ),
    },
    "rag.fail.WEB_ERROR": {
        "zh-TW": "查詢官方資料時暫時失敗，請稍後再試。",
        "en": "Failed to search official sources temporarily. Please try again later.",
        "id": "Pencarian di sumber resmi sementara gagal. Silakan coba lagi nanti.",
        "vi": "Tra cứu nguồn chính thức tạm thời thất bại. Vui lòng thử lại sau.",
        "th": "การค้นหาจากแหล่งข้อมูลทางการล้มเหลวชั่วคราว กรุณาลองใหม่ภายหลัง",
        "ja": "公式情報の検索に一時的に失敗しました。しばらくしてから再度お試しください。",
    },
    "rag.fail.MODEL_REFUSE": {
        "zh-TW": "找到的資料不足以安全回答此問題。請換個方式描述，或必要時就醫。",
        "en": (
            "The available information is not sufficient to answer this question safely. "
            "Please rephrase your question, or seek medical care if needed."
        ),
        "id": (
            "Informasi yang tersedia tidak cukup untuk menjawab pertanyaan ini dengan aman. "
            "Silakan ubah cara Anda bertanya, atau konsultasikan ke tenaga medis jika perlu."
        ),
        "vi": (
            "Thông tin hiện có không đủ để trả lời câu hỏi này một cách an toàn. "
            "Vui lòng diễn đạt lại, hoặc đi khám nếu cần."
        ),
        "th": (
            "ข้อมูลที่มีอยู่ไม่เพียงพอที่จะตอบคำถามนี้อย่างปลอดภัย "
            "กรุณาเปลี่ยนวิธีถาม หรือไปพบแพทย์หากจำเป็น"
        ),
        "ja": (
            "利用可能な情報では、この質問に安全に回答できません。"
            "別の言い方で質問するか、必要であれば医療機関を受診してください。"
        ),
    },
    "agent.rag_prefix": {
        "zh-TW": "以下為 RAG 回應：",
        "en": "The following is a RAG response:",
        "id": "Berikut respons RAG:",
        "vi": "Dưới đây là phản hồi RAG:",
        "th": "ต่อไปนี้คือคำตอบ RAG:",
        "ja": "以下は RAG 応答です：",
    },
    "agent.sources_heading": {
        "zh-TW": "參考資料來源：",
        "en": "References:",
        "id": "Sumber referensi:",
        "vi": "Nguồn tham khảo:",
        "th": "แหล่งอ้างอิง:",
        "ja": "参考資料：",
    },
    "rag.web_source_label": {
        "zh-TW": "網路",
        "en": "Web",
        "id": "Web",
        "vi": "Web",
        "th": "เว็บ",
        "ja": "ウェブ",
    },
    "rag.web_answer_prefix": {
        "zh-TW": "以下參考網路公開資料",
        "en": "The following is based on publicly available web sources",
        "id": "Berikut merujuk pada sumber web publik",
        "vi": "Dưới đây tham khảo dữ liệu công khai trên web",
        "th": "ต่อไปนี้อ้างอิงจากข้อมูลสาธารณะบนเว็บ",
        "ja": "以下は公開ウェブ資料に基づきます",
    },
    "rag.generate_fallback": {
        "zh-TW": "抱歉，我目前找不到相關資料，請稍後再試。",
        "en": "Sorry, I couldn't find relevant information right now. Please try again later.",
        "id": "Maaf, saat ini saya tidak menemukan informasi terkait. Silakan coba lagi nanti.",
        "vi": "Xin lỗi, hiện tôi không tìm thấy thông tin liên quan. Vui lòng thử lại sau.",
        "th": "ขออภัย ขณะนี้ฉันไม่พบข้อมูลที่เกี่ยวข้อง กรุณาลองใหม่ภายหลัง",
        "ja": "申し訳ありません。関連情報が見つかりませんでした。しばらくしてから再度お試しください。",
    },
    # RAG 直通（RAG_DIRECT_REPLY）時附在答案末尾的提醒。
    #
    # 為什麼要有固定文案：不直通時這句話是「請模型記得加」——system prompt
    # 第 4 條要求遇醫療緊急情況提醒尋求專業協助，但實測三題只加了兩題。
    # 醫療提醒不該取決於模型當下的心情，改成程式接上去就是 100%。
    #
    # ⚠ 這段文案是工程預設值，措辭未經醫療專業審閱。上線前請確認用字。
    "rag.professional_advice_notice": {
        "zh-TW": "以上內容僅供參考，無法取代醫師診斷。身體不適或情況緊急請儘速就醫。",
        "en": (
            "The information above is for reference only and cannot replace a doctor's "
            "diagnosis. If you feel unwell or this is an emergency, please seek medical "
            "care promptly."
        ),
        "id": (
            "Informasi di atas hanya sebagai referensi dan tidak dapat menggantikan "
            "diagnosis dokter. Jika Anda merasa tidak enak badan atau dalam keadaan "
            "darurat, segera cari pertolongan medis."
        ),
        "vi": (
            "Thông tin trên chỉ mang tính tham khảo và không thể thay thế chẩn đoán "
            "của bác sĩ. Nếu bạn thấy khó chịu hoặc trong trường hợp khẩn cấp, hãy đi "
            "khám ngay."
        ),
        "th": (
            "ข้อมูลข้างต้นใช้เพื่อการอ้างอิงเท่านั้น ไม่สามารถใช้แทนการวินิจฉัยของแพทย์ได้ "
            "หากรู้สึกไม่สบายหรือเป็นกรณีฉุกเฉิน กรุณาไปพบแพทย์โดยเร็ว"
        ),
        "ja": (
            "上記の内容は参考情報であり、医師の診断に代わるものではありません。"
            "体調がすぐれない場合や緊急時は、早めに医療機関を受診してください。"
        ),
    },
    "line.fallback_ununderstood": {
        "zh-TW": "抱歉，我無法理解您的問題，請重新輸入。",
        "en": "Sorry, I couldn't understand your question. Please try again.",
        "id": "Maaf, saya tidak memahami pertanyaan Anda. Silakan coba lagi.",
        "vi": "Xin lỗi, tôi không hiểu câu hỏi của bạn. Vui lòng nhập lại.",
        "th": "ขออภัย ฉันไม่เข้าใจคำถามของคุณ กรุณาพิมพ์ใหม่",
        "ja": "申し訳ありません。ご質問を理解できませんでした。もう一度入力してください。",
    },
    "line.fallback_process_error": {
        "zh-TW": "抱歉，處理您的訊息時發生錯誤，請稍後再試",
        "en": "Sorry, an error occurred while processing your message. Please try again later.",
        "id": "Maaf, terjadi kesalahan saat memproses pesan Anda. Silakan coba lagi nanti.",
        "vi": "Xin lỗi, đã xảy ra lỗi khi xử lý tin nhắn của bạn. Vui lòng thử lại sau.",
        "th": "ขออภัย เกิดข้อผิดพลาดขณะประมวลผลข้อความของคุณ กรุณาลองใหม่ภายหลัง",
        "ja": "申し訳ありません。メッセージの処理中にエラーが発生しました。しばらくしてから再度お試しください。",
    },
    "location.share_prompt": {
        "zh-TW": (
            "請點擊下方的『分享位置資訊』按鈕傳送您的位置，"
            "我馬上為您尋找附近的醫療院所！"
        ),
        "en": (
            'Please tap the "Share location" button below to send your location, '
            "and I'll find nearby medical facilities for you!"
        ),
        "id": (
            'Silakan ketuk tombol "Bagikan lokasi" di bawah untuk mengirim lokasi Anda, '
            "dan saya akan segera mencari fasilitas medis terdekat!"
        ),
        "vi": (
            'Vui lòng nhấn nút "Chia sẻ vị trí" bên dưới để gửi vị trí của bạn, '
            "tôi sẽ ngay lập tức tìm cơ sở y tế gần bạn!"
        ),
        "th": (
            'กรุณาแตะปุ่ม "แชร์ตำแหน่ง" ด้านล่างเพื่อส่งตำแหน่งของคุณ '
            "แล้วฉันจะค้นหาสถานพยาบาลใกล้เคียงให้ทันที!"
        ),
        "ja": (
            "下の「位置情報を共有」ボタンをタップして位置を送信してください。"
            "すぐに近くの医療機関をお探しします！"
        ),
    },
    "location.share_qr_label": {
        "zh-TW": "分享位置資訊",
        "en": "Share location",
        "id": "Bagikan lokasi",
        "vi": "Chia sẻ vị trí",
        "th": "แชร์ตำแหน่ง",
        "ja": "位置情報を共有",
    },
    # 搜尋已逐級放寬到 50 公里仍無結果，才會走到這裡。因此不再宣稱「功能建置中」
    # ——資料確實查過了，是這個範圍內真的沒有院所。
    "location.no_facility": {
        "zh-TW": (
            "抱歉，已為您搜尋至 {radius_km} 公里範圍，仍找不到醫療院所資料。\n"
            "若情況緊急，請直接撥打 119。"
        ),
        "en": (
            "Sorry, no medical facilities were found within {radius_km} km of your location.\n"
            "If this is an emergency, please call 119 immediately."
        ),
        "id": (
            "Maaf, tidak ditemukan fasilitas medis dalam radius {radius_km} km dari lokasi Anda.\n"
            "Jika ini keadaan darurat, segera hubungi 119."
        ),
        "vi": (
            "Xin lỗi, không tìm thấy cơ sở y tế nào trong bán kính {radius_km} km quanh bạn.\n"
            "Nếu đây là trường hợp khẩn cấp, vui lòng gọi ngay 119."
        ),
        "th": (
            "ขออภัย ไม่พบสถานพยาบาลในรัศมี {radius_km} กม. จากตำแหน่งของคุณ\n"
            "หากเป็นกรณีฉุกเฉิน กรุณาโทร 119 ทันที"
        ),
        "ja": (
            "申し訳ありません。{radius_km} km 以内に医療機関が見つかりませんでした。\n"
            "緊急の場合は 119 に電話してください。"
        ),
    },
    "location.department.title": {
        "zh-TW": "附近的{department}",
        "en": "Nearby {department}",
        "id": "{department} terdekat",
        "vi": "{department} gần đây",
        "th": "{department} ใกล้เคียง",
        "ja": "近くの{department}",
    },
    # 使用者說的科別在健保資料裡不存在時（例如腸胃科屬於內科），必須誠實說明這層
    # 對應，否則使用者會以為系統真的找到了腸胃專科。
    "location.department.alias_note": {
        "zh-TW": "※「{requested}」在健保院所資料中歸類於「{canonical}」，以下為{canonical}院所。",
        "en": '※ "{requested}" is classified under "{canonical}" in the NHI facility data. Results below are {canonical} facilities.',
        "id": '※ "{requested}" diklasifikasikan sebagai "{canonical}" dalam data fasilitas NHI. Hasil di bawah adalah fasilitas {canonical}.',
        "vi": '※ "{requested}" được xếp vào "{canonical}" trong dữ liệu cơ sở y tế NHI. Kết quả bên dưới là các cơ sở {canonical}.',
        "th": "※ \"{requested}\" ถูกจัดอยู่ในหมวด \"{canonical}\" ในข้อมูลสถานพยาบาล NHI ผลลัพธ์ด้านล่างคือสถานพยาบาล{canonical}",
        "ja": "※「{requested}」は健保の医療機関データでは「{canonical}」に分類されます。以下は{canonical}の医療機関です。",
    },
    "location.nearby.found_within": {
        "zh-TW": "已為您找到 {radius_km} 公里內最近的 {count} 間，點擊查看詳細資訊",
        "en": "Found the {count} nearest within {radius_km} km. Tap one for details.",
        "id": "Ditemukan {count} terdekat dalam radius {radius_km} km. Ketuk untuk detail.",
        "vi": "Đã tìm thấy {count} cơ sở gần nhất trong {radius_km} km. Nhấn để xem chi tiết.",
        "th": "พบ {count} แห่งที่ใกล้ที่สุดในรัศมี {radius_km} กม. แตะเพื่อดูรายละเอียด",
        "ja": "{radius_km} km 以内で最も近い {count} 件が見つかりました。タップで詳細を表示します。",
    },
    # {radius_km} 為結果中「最遠院所」的實際距離，不是搜尋階梯的級距，
    # 以免使用者誤以為要跑到級距那麼遠。
    "location.nearby.expanded": {
        "zh-TW": "5 公里內數量不足，已為您擴大範圍，共 {count} 間，最遠約 {radius_km} 公里",
        "en": "Not enough within 5 km, so the search was widened. Found {count}, the furthest about {radius_km} km away.",
        "id": "Tidak cukup dalam 5 km, pencarian diperluas. Ditemukan {count}, terjauh sekitar {radius_km} km.",
        "vi": "Không đủ trong 5 km nên đã mở rộng phạm vi. Tìm thấy {count}, xa nhất khoảng {radius_km} km.",
        "th": "ไม่เพียงพอในรัศมี 5 กม. จึงขยายการค้นหา พบ {count} แห่ง ไกลสุดประมาณ {radius_km} กม.",
        "ja": "5 km 以内では足りないため範囲を広げました。{count} 件、最も遠いもので約 {radius_km} km です。",
    },
    # 50 公里都湊不滿目標筆數：回傳有找到的，並說清楚為什麼只有這幾間。
    "location.nearby.partial": {
        "zh-TW": "已搜尋至 {radius_km} 公里，僅找到 {count} 間，以下為全部結果",
        "en": "Searched up to {radius_km} km and found only {count}. All results are listed below.",
        "id": "Dicari hingga {radius_km} km, hanya ditemukan {count}. Semua hasil di bawah ini.",
        "vi": "Đã tìm trong bán kính {radius_km} km, chỉ thấy {count}. Dưới đây là toàn bộ kết quả.",
        "th": "ค้นหาถึง {radius_km} กม. พบเพียง {count} แห่ง ทั้งหมดแสดงด้านล่าง",
        "ja": "{radius_km} km まで検索しましたが {count} 件のみでした。以下がすべての結果です。",
    },
    # 使用者明確要求「現在有開的」但一家都沒開時的前綴。回「查無院所」是最差的答案，
    # 改為列出最近院所並附上下次開診時間。
    "location.open_now.none": {
        "zh-TW": "附近目前沒有正在營業的院所，以下為最近的院所與下次開診時間",
        "en": "No facilities are open right now. Below are the nearest ones with their next opening times.",
        "id": "Tidak ada fasilitas yang buka saat ini. Berikut yang terdekat beserta jam buka berikutnya.",
        "vi": "Hiện không có cơ sở nào đang mở. Dưới đây là các cơ sở gần nhất kèm giờ mở cửa tiếp theo.",
        "th": "ขณะนี้ไม่มีสถานพยาบาลที่เปิดอยู่ ด้านล่างคือที่ใกล้ที่สุดพร้อมเวลาเปิดครั้งถัดไป",
        "ja": "現在営業中の医療機関はありません。以下は最も近い医療機関と次回の診療開始時刻です。",
    },
    "location.open_now.found": {
        "zh-TW": "為您找到 {count} 間目前營業中的院所，點擊查看詳細資訊",
        "en": "Found {count} facilities open now. Tap one for details.",
        "id": "Ditemukan {count} fasilitas yang buka sekarang. Ketuk untuk detail.",
        "vi": "Đã tìm thấy {count} cơ sở đang mở. Nhấn để xem chi tiết.",
        "th": "พบสถานพยาบาลที่เปิดอยู่ {count} แห่ง แตะเพื่อดูรายละเอียด",
        "ja": "現在営業中の医療機関が {count} 件見つかりました。タップで詳細を表示します。",
    },
    "location.department.none": {
        "zh-TW": (
            "抱歉，您附近 {radius_km} 公里內找不到有「{department}」的醫療院所。\n"
            "建議您改以其他科別搜尋，或直接詢問特定院所名稱。"
        ),
        "en": (
            'Sorry, no facility with "{department}" was found within {radius_km} km of you.\n'
            "Try another specialty, or ask about a specific facility by name."
        ),
        "id": (
            'Maaf, tidak ditemukan fasilitas dengan "{department}" dalam radius {radius_km} km.\n'
            "Coba spesialisasi lain, atau tanyakan nama fasilitas tertentu."
        ),
        "vi": (
            'Xin lỗi, không tìm thấy cơ sở nào có "{department}" trong bán kính {radius_km} km.\n'
            "Hãy thử chuyên khoa khác, hoặc hỏi theo tên cơ sở cụ thể."
        ),
        "th": (
            "ขออภัย ไม่พบสถานพยาบาลที่มี \"{department}\" ในรัศมี {radius_km} กม.\n"
            "ลองค้นหาแผนกอื่น หรือสอบถามชื่อสถานพยาบาลโดยตรง"
        ),
        "ja": (
            "申し訳ありません。お近く {radius_km} km 以内に「{department}」のある医療機関が見つかりませんでした。\n"
            "他の診療科で検索するか、特定の医療機関名でお尋ねください。"
        ),
    },
    # 解析不出科別時不要退化成「搜全部」，那會讓使用者誤以為系統懂他要的科別。
    "location.department.unknown": {
        "zh-TW": (
            "抱歉，我不確定「{department}」對應到哪一個診療科別。\n"
            "您可以改說常見科別，例如：內科、外科、兒科、牙科、耳鼻喉科、骨科、皮膚科、眼科、婦產科、中醫。"
        ),
        "en": (
            'Sorry, I am not sure which specialty "{department}" maps to.\n'
            "Try a common one such as internal medicine, surgery, pediatrics, dentistry, ENT, orthopedics, dermatology, ophthalmology, obstetrics & gynecology, or Chinese medicine."
        ),
        "id": (
            'Maaf, saya tidak yakin "{department}" termasuk spesialisasi apa.\n'
            "Coba sebutkan yang umum seperti penyakit dalam, bedah, anak, gigi, THT, ortopedi, kulit, mata, kandungan, atau pengobatan Tionghoa."
        ),
        "vi": (
            'Xin lỗi, tôi không chắc "{department}" thuộc chuyên khoa nào.\n'
            "Hãy thử các khoa phổ biến như nội khoa, ngoại khoa, nhi, răng hàm mặt, tai mũi họng, chấn thương chỉnh hình, da liễu, mắt, sản phụ khoa hoặc Đông y."
        ),
        "th": (
            "ขออภัย ฉันไม่แน่ใจว่า \"{department}\" ตรงกับแผนกใด\n"
            "ลองระบุแผนกที่พบบ่อย เช่น อายุรกรรม ศัลยกรรม กุมารเวช ทันตกรรม หู คอ จมูก กระดูก ผิวหนัง ตา สูตินรีเวช หรือแพทย์แผนจีน"
        ),
        "ja": (
            "申し訳ありません。「{department}」がどの診療科に該当するか判断できませんでした。\n"
            "内科・外科・小児科・歯科・耳鼻咽喉科・整形外科・皮膚科・眼科・産婦人科・漢方など、一般的な診療科でお試しください。"
        ),
    },
    "location.type.title": {
        "zh-TW": "附近的{type}",
        "en": "Nearby {type}",
        "id": "{type} terdekat",
        "vi": "{type} gần đây",
        "th": "{type} ใกล้เคียง",
        "ja": "近くの{type}",
    },
    # 解析不出院所類型時不要退化成「搜全部」，否則使用者會誤以為系統聽懂了他要的類型。
    "location.type.unknown": {
        "zh-TW": (
            "抱歉，我不確定「{facility_type}」對應到哪一種院所類型。\n"
            "您可以改說：醫院、診所、藥局。"
        ),
        "en": (
            'Sorry, I am not sure what kind of facility "{facility_type}" refers to.\n'
            "Try one of: hospital, clinic, or pharmacy."
        ),
        "id": (
            'Maaf, saya tidak yakin jenis fasilitas apa yang dimaksud dengan "{facility_type}".\n'
            "Coba salah satu dari: rumah sakit, klinik, atau apotek."
        ),
        "vi": (
            'Xin lỗi, tôi không chắc "{facility_type}" thuộc loại cơ sở y tế nào.\n'
            "Hãy thử một trong các loại: bệnh viện, phòng khám hoặc nhà thuốc."
        ),
        "th": (
            "ขออภัย ฉันไม่แน่ใจว่า \"{facility_type}\" ตรงกับประเภทสถานพยาบาลใด\n"
            "ลองระบุประเภทใดประเภทหนึ่ง เช่น โรงพยาบาล คลินิก หรือร้านขายยา"
        ),
        "ja": (
            "申し訳ありません。「{facility_type}」がどの院所種別に該当するか判断できませんでした。\n"
            "病院・診療所・薬局のいずれかでお試しください。"
        ),
    },
    # 資料庫僅收錄 116 家藥局（藥師自營 92 + 藥劑生自營 24），遠低於全台實際數千家健保特約藥局，
    # 因此搜尋藥局幾乎必然「查無結果」。若沿用通用的查無院所訊息，會讓使用者誤以為附近真的沒有
    # 藥局；這則訊息必須誠實把原因歸給「本系統資料覆蓋率有限」而非地理位置，並提供改用藥局
    # 名稱查詢的替代做法，因此獨立成專屬 key，不與 location.department.none 共用文案。
    "location.type.pharmacy_none": {
        "zh-TW": (
            "抱歉，本系統目前收錄的藥局資料有限，您附近 {radius_km} 公里內暫時查無登記中的藥局，"
            "這並不代表附近真的沒有藥局。\n"
            "建議您改以藥局名稱查詢（例如「OO藥局」），會更容易找到您要的藥局。"
        ),
        "en": (
            "Sorry, our pharmacy data is currently limited, so no registered pharmacy was found "
            "within {radius_km} km of you — this does not mean there are no pharmacies nearby.\n"
            "Try searching by the pharmacy's name (e.g. \"XX Pharmacy\") instead, which is more "
            "likely to find a match."
        ),
        "id": (
            "Maaf, data apotek dalam sistem kami masih terbatas, sehingga tidak ditemukan apotek "
            "terdaftar dalam radius {radius_km} km dari Anda — ini bukan berarti tidak ada apotek "
            "di sekitar Anda.\n"
            "Coba cari dengan nama apotek (misalnya \"Apotek XX\"), yang lebih mungkin ditemukan."
        ),
        "vi": (
            "Xin lỗi, dữ liệu nhà thuốc trong hệ thống của chúng tôi vẫn còn hạn chế, nên không "
            "tìm thấy nhà thuốc nào được đăng ký trong bán kính {radius_km} km quanh bạn — điều "
            "này không có nghĩa là gần bạn không có nhà thuốc.\n"
            "Hãy thử tìm theo tên nhà thuốc (ví dụ: \"Nhà thuốc XX\"), khả năng tìm thấy sẽ cao hơn."
        ),
        "th": (
            "ขออภัย ข้อมูลร้านขายยาในระบบของเรายังมีจำกัด จึงไม่พบร้านขายยาที่ลงทะเบียนไว้ในรัศมี "
            "{radius_km} กม. จากคุณ ซึ่งไม่ได้หมายความว่าแถวนั้นไม่มีร้านขายยาจริง ๆ\n"
            "ลองค้นหาด้วยชื่อร้านขายยาโดยตรง (เช่น \"ร้านขายยา XX\") จะมีโอกาสพบมากกว่า"
        ),
        "ja": (
            "申し訳ありません。本システムに登録されている薬局データはまだ限られており、"
            "{radius_km} km 以内に登録済みの薬局が見つかりませんでした。これは近くに薬局が"
            "実際にないという意味ではありません。\n"
            "薬局名で直接検索（例：「〇〇薬局」）していただくと見つかりやすくなります。"
        ),
    },
    # 與 pharmacy_none 同源的資料缺口，但情境相反：這則用在「查得到藥局，卻遠到
    # 不可能是使用者心中的『附近』」（實測台北車站最近一家藥局在 18 公里外）。
    # 此時卡片本身看起來完全正常，若不加說明，使用者會相信步行範圍內真的沒有藥局。
    # 語氣比照 pharmacy_none：把原因歸給本系統的收錄範圍，而不是地理事實，
    # 並同樣給出「改用藥局名稱查詢」這個可行動的替代做法。
    "location.type.pharmacy_data_gap": {
        "zh-TW": (
            "※ 本系統收錄的藥局資料有限，最近一家距離約 {radius_km} 公里，"
            "您附近很可能還有未被收錄的藥局。建議改以藥局名稱查詢（例如「OO藥局」）。"
        ),
        "en": (
            "※ Our pharmacy data is limited — the nearest listed one is about {radius_km} km away, "
            "and there are likely unlisted pharmacies much closer to you. "
            "Try searching by the pharmacy's name (e.g. \"XX Pharmacy\") instead."
        ),
        "id": (
            "※ Data apotek kami terbatas — yang terdekat sekitar {radius_km} km, "
            "dan kemungkinan besar masih ada apotek lain lebih dekat yang belum terdaftar. "
            "Coba cari dengan nama apotek (misalnya \"Apotek XX\")."
        ),
        "vi": (
            "※ Dữ liệu nhà thuốc của chúng tôi còn hạn chế — nhà thuốc gần nhất cách khoảng "
            "{radius_km} km, và rất có thể còn nhà thuốc gần hơn chưa được ghi nhận. "
            "Hãy thử tìm theo tên nhà thuốc (ví dụ: \"Nhà thuốc XX\")."
        ),
        "th": (
            "※ ข้อมูลร้านขายยาในระบบของเรามีจำกัด ร้านที่ใกล้ที่สุดอยู่ห่างประมาณ {radius_km} กม. "
            "และน่าจะยังมีร้านขายยาที่ใกล้กว่านี้ซึ่งยังไม่ได้บันทึกไว้ "
            "ลองค้นหาด้วยชื่อร้านขายยา (เช่น \"ร้านขายยา XX\")"
        ),
        "ja": (
            "※ 本システムの薬局データは限られており、最も近い登録薬局でも約 {radius_km} km 先です。"
            "実際にはもっと近くに未登録の薬局がある可能性が高いため、"
            "薬局名での検索（例：「〇〇薬局」）もお試しください。"
        ),
    },
    # ── 每日醫療消息卡（medical-news-push）────────────────────────
    "news.tier1_header": {
        "zh-TW": "與您正在服用的藥有關",
        "en": "About a medicine you are taking",
        "id": "Terkait obat yang Anda konsumsi",
        "vi": "Liên quan đến thuốc bạn đang dùng",
        "th": "เกี่ยวกับยาที่คุณกำลังใช้",
        "ja": "服用中のお薬に関するお知らせ",
    },
    "news.tier2_header": {
        "zh-TW": "今日醫療小知識",
        "en": "Today's health note",
        "id": "Info kesehatan hari ini",
        "vi": "Kiến thức y tế hôm nay",
        "th": "เกร็ดสุขภาพวันนี้",
        "ja": "今日の健康メモ",
    },
    "news.shared_header": {
        "zh-TW": "{name} 分享給您",
        "en": "{name} shared this with you",
        "id": "{name} membagikan ini kepada Anda",
        "vi": "{name} đã chia sẻ với bạn",
        "th": "{name} แชร์ให้คุณ",
        "ja": "{name} さんがシェアしました",
    },
    "news.drug_label": {
        "zh-TW": "相關藥品：{name}",
        "en": "Related medicine: {name}",
        "id": "Obat terkait: {name}",
        "vi": "Thuốc liên quan: {name}",
        "th": "ยาที่เกี่ยวข้อง: {name}",
        "ja": "対象のお薬：{name}",
    },
    "news.consult_professional": {
        "zh-TW": "請與您的醫師或藥師確認，不要自行改變用藥。",
        "en": "Please confirm with your doctor or pharmacist. Do not change your medicine on your own.",
        "id": "Harap konfirmasi dengan dokter atau apoteker Anda.",
        "vi": "Vui lòng xác nhận với bác sĩ hoặc dược sĩ của bạn.",
        "th": "โปรดปรึกษาแพทย์หรือเภสัชกรของคุณ",
        "ja": "医師または薬剤師にご確認ください。",
    },
    "news.source_button": {
        "zh-TW": "查看原文",
        "en": "View source",
        "id": "Lihat sumber",
        "vi": "Xem nguồn",
        "th": "ดูแหล่งที่มา",
        "ja": "原文を見る",
    },
    "news.share_button": {
        "zh-TW": "認同，分享給家人",
        "en": "Agree, share with family",
        "id": "Setuju, bagikan ke keluarga",
        "vi": "Đồng ý, chia sẻ với gia đình",
        "th": "เห็นด้วย แชร์ให้ครอบครัว",
        "ja": "共感、家族にシェア",
    },
    "news.share_display": {
        "zh-TW": "分享給家人",
        "en": "Share with family",
        "id": "Bagikan ke keluarga",
        "vi": "Chia sẻ với gia đình",
        "th": "แชร์ให้ครอบครัว",
        "ja": "家族にシェア",
    },
    "news.shared_ok": {
        "zh-TW": "已分享給 {count} 位家人。",
        "en": "Shared with {count} family member(s).",
        "id": "Dibagikan ke {count} anggota keluarga.",
        "vi": "Đã chia sẻ với {count} người thân.",
        "th": "แชร์ให้สมาชิกครอบครัว {count} คนแล้ว",
        "ja": "{count} 名のご家族にシェアしました。",
    },
    "news.shared_none": {
        "zh-TW": "這則消息您的家人都已經收到了。",
        "en": "Your family members have already received this.",
        "id": "Keluarga Anda sudah menerima ini.",
        "vi": "Người thân của bạn đã nhận được tin này.",
        "th": "ครอบครัวของคุณได้รับข่าวนี้แล้ว",
        "ja": "このお知らせはご家族が既に受け取っています。",
    },
    "news.no_family": {
        "zh-TW": "您的家庭成員清單目前是空的，先邀請家人加入就能分享給他們。",
        "en": "Your family list is empty. Invite family members first to share with them.",
        "id": "Daftar keluarga Anda kosong. Undang anggota keluarga terlebih dahulu.",
        "vi": "Danh sách gia đình của bạn đang trống. Hãy mời người thân trước.",
        "th": "รายชื่อครอบครัวของคุณว่างอยู่ กรุณาเชิญสมาชิกก่อน",
        "ja": "ご家族リストが空です。まずご家族を招待してください。",
    },
    "news.share_expired": {
        "zh-TW": "這則消息太久了，已經無法分享。",
        "en": "This item is too old to share.",
        "id": "Berita ini terlalu lama untuk dibagikan.",
        "vi": "Tin này đã quá cũ để chia sẻ.",
        "th": "ข่าวนี้เก่าเกินกว่าจะแชร์ได้",
        "ja": "このお知らせは古いためシェアできません。",
    },
    "news.share_limit_reached": {
        "zh-TW": "今天分享的次數已達上限，明天再繼續喔。",
        "en": "You have reached today's sharing limit. Please try again tomorrow.",
        "id": "Anda telah mencapai batas berbagi hari ini.",
        "vi": "Bạn đã đạt giới hạn chia sẻ hôm nay.",
        "th": "คุณถึงขีดจำกัดการแชร์ของวันนี้แล้ว",
        "ja": "本日のシェア上限に達しました。",
    },
    "news.alt_tier1": {
        "zh-TW": "與您用藥有關的消息",
        "en": "News about your medicine",
        "id": "Berita tentang obat Anda",
        "vi": "Tin về thuốc của bạn",
        "th": "ข่าวเกี่ยวกับยาของคุณ",
        "ja": "お薬に関するお知らせ",
    },
    "news.alt_tier2": {
        "zh-TW": "今日醫療小知識",
        "en": "Today's health note",
        "id": "Info kesehatan hari ini",
        "vi": "Kiến thức y tế hôm nay",
        "th": "เกร็ดสุขภาพวันนี้",
        "ja": "今日の健康メモ",
    },
    "news.alt_shared": {
        "zh-TW": "家人分享的消息",
        "en": "Shared by family",
        "id": "Dibagikan keluarga",
        "vi": "Người thân chia sẻ",
        "th": "แชร์โดยครอบครัว",
        "ja": "家族からのシェア",
    },
    "meds.recorded": {
        "zh-TW": "已記錄您的服藥狀態！",
        "en": "Your medication status has been recorded!",
        "id": "Status obat Anda telah dicatat!",
        "vi": "Trạng thái uống thuốc của bạn đã được ghi nhận!",
        "th": "บันทึกสถานะการใช้ยาของคุณแล้ว!",
        "ja": "服薬状況を記録しました！",
    },
    "meds.already_recorded": {
        "zh-TW": "此服藥提醒先前已完成紀錄囉！祝您身體健康！",
        "en": "This medication reminder was already recorded. Wishing you good health!",
        "id": "Pengingat obat ini sudah pernah dicatat sebelumnya. Semoga Anda sehat selalu!",
        "vi": "Lời nhắc uống thuốc này đã được ghi nhận trước đó. Chúc bạn sức khỏe!",
        "th": "การแจ้งเตือนการใช้ยานี้ได้บันทึกไว้แล้วก่อนหน้านี้ ขอให้สุขภาพแข็งแรง!",
        "ja": "この服薬リマインダーはすでに記録済みです。ご健康をお祈りします！",
    },
    # 逐藥確認未到齊時的純文字回覆（spec「逐藥確認」）：不耗推播額度，靠
    # reply token 直接回這則，列出已記錄與尚未確認的藥名，讓使用者知道
    # 「按有生效」而不必等下一則卡片。
    "meds.progress": {
        "zh-TW": "已記錄：{taken}。還有 {count} 種：{remaining}",
        "en": "Recorded: {taken}. {count} more to go: {remaining}",
        "id": "Tercatat: {taken}. Masih {count} lagi: {remaining}",
        "vi": "Đã ghi nhận: {taken}. Còn {count} loại: {remaining}",
        "th": "บันทึกแล้ว: {taken} เหลืออีก {count} รายการ: {remaining}",
        "ja": "記録しました：{taken}。残り {count} 種：{remaining}",
    },
    # 逐藥確認未到齊但這次沒有任何藥被標記已服用（例如查名稱失敗退化回空
    # 清單）：不套用 meds.progress 那套「已記錄：{taken}」的措辭，避免出現
    # 「已記錄：已記錄您的服藥狀態！」這種疊字句，改用只講「還有幾種待確
    # 認」的獨立句型。
    "meds.progress_no_taken": {
        "zh-TW": "還有 {count} 種尚未確認：{remaining}",
        "en": "{count} still unconfirmed: {remaining}",
        "id": "Masih {count} belum dikonfirmasi: {remaining}",
        "vi": "Còn {count} loại chưa xác nhận: {remaining}",
        "th": "ยังไม่ยืนยันอีก {count} รายการ: {remaining}",
        "ja": "未確認が {count} 種あります：{remaining}",
    },
    # 逐藥確認後同一筆規則當日已無有效藥品待確認（例如藥被停用）：此時只
    # 剩「已記錄」這句，不該出現「還有 0 種：」這種空清單的殘影。
    "meds.progress_none_left": {
        "zh-TW": "已記錄：{taken}",
        "en": "Recorded: {taken}",
        "id": "Tercatat: {taken}",
        "vi": "Đã ghi nhận: {taken}",
        "th": "บันทึกแล้ว: {taken}",
        "ja": "記録しました：{taken}",
    },
    "voice.enabled": {
        "zh-TW": "已開啟語音回覆",
        "en": "Voice reply has been enabled",
        "id": "Balasan suara telah diaktifkan",
        "vi": "Đã bật trả lời bằng giọng nói",
        "th": "เปิดการตอบด้วยเสียงแล้ว",
        "ja": "音声返信をオンにしました",
    },
    "voice.disabled": {
        "zh-TW": "已關閉語音回覆",
        "en": "Voice reply has been disabled",
        "id": "Balasan suara telah dinonaktifkan",
        "vi": "Đã tắt trả lời bằng giọng nói",
        "th": "ปิดการตอบด้วยเสียงแล้ว",
        "ja": "音声返信をオフにしました",
    },
    "voice.need_login": {
        "zh-TW": "請先開啟「家庭中心」完成登入後再設定語音回覆",
        "en": 'Please open "Family Center" and sign in before enabling voice reply.',
        "id": (
            'Silakan buka "Pusat Keluarga" dan masuk terlebih dahulu '
            "sebelum mengatur balasan suara."
        ),
        "vi": (
            'Vui lòng mở "Trung tâm gia đình" và đăng nhập trước khi '
            "cài đặt trả lời bằng giọng nói."
        ),
        "th": (
            'กรุณาเปิด "ศูนย์ครอบครัว" และเข้าสู่ระบบก่อนตั้งค่าการตอบด้วยเสียง'
        ),
        "ja": "音声返信を設定する前に、「家族センター」を開いてログインしてください。",
    },
    # --- Flex：官方入口 ---
    "flex.site.alt": {
        "zh-TW": "CARE 官方入口",
        "en": "CARE official entry",
        "id": "Pintu masuk resmi CARE",
        "vi": "Cổng chính thức CARE",
        "th": "ทางเข้าอย่างเป็นทางการของ CARE",
        "ja": "CARE 公式入口",
    },
    "flex.site.title": {
        "zh-TW": "官方入口",
        "en": "Official entry",
        "id": "Pintu masuk resmi",
        "vi": "Cổng chính thức",
        "th": "ทางเข้าอย่างเป็นทางการ",
        "ja": "公式入口",
    },
    "flex.site.desc": {
        "zh-TW": "點擊下方按鈕開啟 CARE 服務。",
        "en": "Tap the button below to open CARE.",
        "id": "Ketuk tombol di bawah untuk membuka CARE.",
        "vi": "Nhấn nút bên dưới để mở CARE.",
        "th": "แตะปุ่มด้านล่างเพื่อเปิด CARE",
        "ja": "下のボタンをタップして CARE を開いてください。",
    },
    "flex.site.button": {
        "zh-TW": "開啟 CARE",
        "en": "Open CARE",
        "id": "Buka CARE",
        "vi": "Mở CARE",
        "th": "เปิด CARE",
        "ja": "CARE を開く",
    },
    # --- Flex：醫療院所共用 ---
    "flex.facility.eyebrow": {
        "zh-TW": "醫療院所",
        "en": "Medical facilities",
        "id": "Fasilitas medis",
        "vi": "Cơ sở y tế",
        "th": "สถานพยาบาล",
        "ja": "医療機関",
    },
    "flex.facility.alt": {
        "zh-TW": "醫療院所查詢結果",
        "en": "Medical facility search results",
        "id": "Hasil pencarian fasilitas medis",
        "vi": "Kết quả tìm kiếm cơ sở y tế",
        "th": "ผลการค้นหาสถานพยาบาล",
        "ja": "医療機関の検索結果",
    },
    "flex.facility.title.nearby": {
        "zh-TW": "附近醫療院所",
        "en": "Nearby facilities",
        "id": "Fasilitas terdekat",
        "vi": "Cơ sở y tế gần đây",
        "th": "สถานพยาบาลใกล้เคียง",
        "ja": "近くの医療機関",
    },
    "flex.facility.title.candidates": {
        "zh-TW": "找到多筆相似院所",
        "en": "Multiple matches found",
        "id": "Ditemukan beberapa yang serupa",
        "vi": "Tìm thấy nhiều kết quả tương tự",
        "th": "พบสถานพยาบาลที่คล้ายกันหลายแห่ง",
        "ja": "類似する医療機関が複数あります",
    },
    "flex.facility.subtitle.nearby": {
        "zh-TW": "為您找到附近 {count} 間醫療院所，點擊查看詳細資訊",
        "en": "Found {count} facilities nearby. Tap one for details.",
        "id": "Ditemukan {count} fasilitas terdekat. Ketuk untuk detail.",
        "vi": "Đã tìm thấy {count} cơ sở gần đây. Nhấn để xem chi tiết.",
        "th": "พบสถานพยาบาลใกล้เคียง {count} แห่ง แตะเพื่อดูรายละเอียด",
        "ja": "近くに {count} 件見つかりました。タップで詳細を表示します。",
    },
    "flex.facility.subtitle.candidates": {
        "zh-TW": "為您找到 {count} 間相似院所，點擊查看詳細資訊",
        "en": "Found {count} similar facilities. Tap one for details.",
        "id": "Ditemukan {count} fasilitas serupa. Ketuk untuk detail.",
        "vi": "Đã tìm thấy {count} cơ sở tương tự. Nhấn để xem chi tiết.",
        "th": "พบสถานพยาบาลที่คล้ายกัน {count} แห่ง แตะเพื่อดูรายละเอียด",
        "ja": "類似する医療機関が {count} 件見つかりました。タップで詳細を表示します。",
    },
    "flex.facility.overflow": {
        "zh-TW": "結果超過顯示上限，您可以提供更明確的地區或完整名稱以縮小範圍。",
        "en": (
            "More results than can be shown. Provide a more specific area or "
            "the full name to narrow the search."
        ),
        "id": (
            "Hasil melebihi batas tampilan. Sebutkan area yang lebih spesifik "
            "atau nama lengkap untuk mempersempit pencarian."
        ),
        "vi": (
            "Kết quả vượt quá giới hạn hiển thị. Vui lòng cung cấp khu vực cụ thể "
            "hơn hoặc tên đầy đủ để thu hẹp phạm vi."
        ),
        "th": (
            "ผลลัพธ์เกินจำนวนที่แสดงได้ กรุณาระบุพื้นที่ให้ชัดเจนขึ้น "
            "หรือระบุชื่อเต็มเพื่อจำกัดการค้นหา"
        ),
        "ja": (
            "表示上限を超えました。地域をより具体的に指定するか、"
            "正式名称を入力して絞り込んでください。"
        ),
    },
    "flex.facility.unknown_name": {
        "zh-TW": "未知名稱",
        "en": "Unknown name",
        "id": "Nama tidak diketahui",
        "vi": "Tên không xác định",
        "th": "ไม่ทราบชื่อ",
        "ja": "名称不明",
    },
    "flex.facility.no_address": {
        "zh-TW": "暫無地址資訊",
        "en": "No address available",
        "id": "Alamat tidak tersedia",
        "vi": "Chưa có thông tin địa chỉ",
        "th": "ไม่มีข้อมูลที่อยู่",
        "ja": "住所情報がありません",
    },
    "flex.facility.fallback_name": {
        "zh-TW": "該院所",
        "en": "this facility",
        "id": "fasilitas ini",
        "vi": "cơ sở này",
        "th": "สถานพยาบาลนี้",
        "ja": "この医療機関",
    },
    "flex.facility.distance_km": {
        "zh-TW": "距離 {value} 公里",
        "en": "{value} km away",
        "id": "Jarak {value} km",
        "vi": "Cách {value} km",
        "th": "ห่าง {value} กม.",
        "ja": "約 {value} km",
    },
    "flex.facility.distance_m": {
        "zh-TW": "距離 {value} 公尺",
        "en": "{value} m away",
        "id": "Jarak {value} m",
        "vi": "Cách {value} m",
        "th": "ห่าง {value} ม.",
        "ja": "約 {value} m",
    },
    "flex.facility.distance_unknown": {
        "zh-TW": "距離未知",
        "en": "Distance unknown",
        "id": "Jarak tidak diketahui",
        "vi": "Không rõ khoảng cách",
        "th": "ไม่ทราบระยะทาง",
        "ja": "距離不明",
    },
    "flex.status.open": {
        "zh-TW": "營業中",
        "en": "Open now",
        "id": "Sedang buka",
        "vi": "Đang mở cửa",
        "th": "เปิดอยู่",
        "ja": "営業中",
    },
    "flex.status.unknown": {
        "zh-TW": "營業時間未提供",
        "en": "Hours not available",
        "id": "Jam buka tidak tersedia",
        "vi": "Chưa có giờ mở cửa",
        "th": "ไม่มีข้อมูลเวลาทำการ",
        "ja": "営業時間の情報なし",
    },
    # 午休中與今日已結束刻意分開：一個是「等一下再來」，一個是「改天再來」，
    # 對使用者是完全不同的決定，不可都寫成「休診中」。
    "flex.status.break": {
        "zh-TW": "午休中",
        "en": "On midday break",
        "id": "Istirahat siang",
        "vi": "Nghỉ trưa",
        "th": "พักกลางวัน",
        "ja": "昼休み中",
    },
    # 與「午休中」分開：凌晨三點是尚未開診，不是午休。
    "flex.status.before_open": {
        "zh-TW": "今日尚未開診",
        "en": "Not open yet today",
        "id": "Belum buka hari ini",
        "vi": "Hôm nay chưa mở",
        "th": "วันนี้ยังไม่เปิด",
        "ja": "本日はまだ開始前",
    },
    "flex.status.closed_today": {
        "zh-TW": "今日已結束",
        "en": "Closed for today",
        "id": "Sudah tutup hari ini",
        "vi": "Đã hết giờ hôm nay",
        "th": "ปิดแล้ววันนี้",
        "ja": "本日の診療は終了",
    },
    "flex.status.closed_day": {
        "zh-TW": "今日休診",
        "en": "Closed today",
        "id": "Tutup hari ini",
        "vi": "Hôm nay không làm việc",
        "th": "วันนี้ปิดทำการ",
        "ja": "本日休診",
    },
    # 刻意不寫「24 小時」：資料只記載該院所設有急診科別，未記載急診開放時間，
    # 宣稱營業時間屬於編造。
    "flex.status.emergency": {
        "zh-TW": "設有急診",
        "en": "Has emergency dept.",
        "id": "Ada unit gawat darurat",
        "vi": "Có khoa cấp cứu",
        "th": "มีแผนกฉุกเฉิน",
        "ja": "救急外来あり",
    },
    "flex.status.call_ahead": {
        "zh-TW": "請先電話洽詢",
        "en": "Call ahead",
        "id": "Hubungi dahulu",
        "vi": "Vui lòng gọi trước",
        "th": "กรุณาโทรสอบถามก่อน",
        "ja": "事前に電話確認",
    },
    "flex.status.next_open_today": {
        "zh-TW": "今日 {time} 開診",
        "en": "Opens today at {time}",
        "id": "Buka hari ini {time}",
        "vi": "Mở lại hôm nay {time}",
        "th": "เปิดวันนี้ {time}",
        "ja": "本日 {time} 開始",
    },
    "flex.status.next_open_day": {
        "zh-TW": "{day} {time} 開診",
        "en": "Opens {day} at {time}",
        "id": "Buka {day} {time}",
        "vi": "Mở {day} {time}",
        "th": "เปิด{day} {time}",
        "ja": "{day} {time} 開始",
    },
    "flex.facility.unspecified_department": {
        "zh-TW": "此院所資料未載明科別，是依距離補列的鄰近選項，建議先去電確認有無此診。",
        "en": (
            "This facility lists no specialty; it is included as a nearby option "
            "by distance. Please call ahead to confirm."
        ),
        "id": (
            "Fasilitas ini tidak mencantumkan spesialisasi; ditampilkan sebagai "
            "opsi terdekat. Sebaiknya telepon dulu untuk memastikan."
        ),
        "vi": (
            "Cơ sở này không ghi chuyên khoa; được đưa vào theo khoảng cách. "
            "Vui lòng gọi trước để xác nhận."
        ),
        "th": (
            "สถานพยาบาลนี้ไม่ได้ระบุแผนก แสดงเป็นตัวเลือกใกล้เคียงตามระยะทาง "
            "แนะนำให้โทรสอบถามก่อน"
        ),
        "ja": (
            "この医療機関は診療科の記載がなく、距離順で補足表示しています。"
            "受診前に電話でご確認ください。"
        ),
    },
    "flex.facility.note": {
        "zh-TW": "院所註記：{note}",
        "en": "Facility note: {note}",
        "id": "Catatan fasilitas: {note}",
        "vi": "Ghi chú cơ sở: {note}",
        "th": "หมายเหตุสถานพยาบาล: {note}",
        "ja": "医療機関の備考：{note}",
    },
    "flex.button.call": {
        "zh-TW": "撥打電話",
        "en": "Call",
        "id": "Telepon",
        "vi": "Gọi điện",
        "th": "โทร",
        "ja": "電話をかける",
    },
    "flex.button.map": {
        "zh-TW": "前往地圖",
        "en": "Directions",
        "id": "Rute",
        "vi": "Chỉ đường",
        "th": "เส้นทาง",
        "ja": "地図を開く",
    },
    "flex.action.detail": {
        "zh-TW": "查看詳情",
        "en": "View details",
        "id": "Lihat detail",
        "vi": "Xem chi tiết",
        "th": "ดูรายละเอียด",
        "ja": "詳細を見る",
    },
    "flex.action.detail_display": {
        "zh-TW": "查看 {name} 的詳細資訊",
        "en": "View details for {name}",
        "id": "Lihat detail {name}",
        "vi": "Xem chi tiết của {name}",
        "th": "ดูรายละเอียดของ {name}",
        "ja": "{name} の詳細を見る",
    },
    # --- Flex：院所詳情 ---
    "flex.detail.alt": {
        "zh-TW": "{name}詳細資訊",
        "en": "{name} details",
        "id": "Detail {name}",
        "vi": "Chi tiết {name}",
        "th": "รายละเอียด {name}",
        "ja": "{name} の詳細",
    },
    "flex.detail.hours": {
        "zh-TW": "營業時間",
        "en": "Opening hours",
        "id": "Jam buka",
        "vi": "Giờ mở cửa",
        "th": "เวลาทำการ",
        "ja": "営業時間",
    },
    "flex.detail.departments": {
        "zh-TW": "診療科別（共 {count} 項）",
        "en": "Departments ({count})",
        "id": "Departemen ({count})",
        "vi": "Chuyên khoa ({count})",
        "th": "แผนก ({count})",
        "ja": "診療科（{count} 件）",
    },

    "flex.detail.no_data": {
        "zh-TW": "無資料",
        "en": "No data",
        "id": "Tidak ada data",
        "vi": "Không có dữ liệu",
        "th": "ไม่มีข้อมูล",
        "ja": "データなし",
    },
    # --- Flex：門診表（時段 × 星期的矩陣）---
    "flex.detail.clinic_table": {
        "zh-TW": "門診表",
        "en": "Schedule",
        "id": "Jadwal",
        "vi": "Lịch khám",
        "th": "ตาราง",
        "ja": "診療表",
    },
    "flex.detail.period.morning": {
        "zh-TW": "早診",
        "en": "Morning",
        "id": "Pagi",
        "vi": "Sáng",
        "th": "เช้า",
        "ja": "午前",
    },
    "flex.detail.period.afternoon": {
        "zh-TW": "午診",
        "en": "Afternoon",
        "id": "Siang",
        "vi": "Chiều",
        "th": "บ่าย",
        "ja": "午後",
    },
    "flex.detail.period.evening": {
        "zh-TW": "晚診",
        "en": "Evening",
        "id": "Malam",
        "vi": "Tối",
        "th": "เย็น",
        "ja": "夜間",
    },
    # --- 掛號提醒卡片（appointment_flex）---
    #
    # 隱私邊界：推播只含日期時間與醫院名稱，任何一則都不得出現科別、醫師、看診號。
    # 家屬每次門診最多收三則，文案裡的每個字都會躺在他們的聊天室列表上。
    "flex.appt.when": {
        "zh-TW": "{month}/{day}（{weekday}）{time}",
        "en": "{weekday} {month}/{day}, {time}",
        "id": "{weekday}, {day}/{month} pukul {time}",
        "vi": "{time} {weekday}, {day}/{month}",
        "th": "{weekday} {day}/{month} เวลา {time} น.",
        "ja": "{month}/{day}（{weekday}）{time}",
    },
    "flex.appt.fallback_name": {
        "zh-TW": "您的家人",
        "en": "Your family member",
        "id": "Anggota keluarga Anda",
        "vi": "Người thân của bạn",
        "th": "สมาชิกในครอบครัวของคุณ",
        "ja": "ご家族",
    },
    "flex.appt.you": {
        "zh-TW": "您",
        "en": "you",
        "id": "Anda",
        "vi": "bạn",
        "th": "คุณ",
        "ja": "あなた",
    },
    "flex.appt.patient_label": {
        "zh-TW": "{name} 的門診",
        "en": "{name}'s appointment",
        "id": "Jadwal periksa {name}",
        "vi": "Lịch khám của {name}",
        "th": "นัดหมายของ {name}",
        "ja": "{name} さんの受診",
    },
    "flex.appt.button.depart": {
        "zh-TW": "我已出發",
        "en": "I'm on my way",
        "id": "Saya sudah berangkat",
        "vi": "Tôi đã xuất phát",
        "th": "ออกเดินทางแล้ว",
        "ja": "出発しました",
    },
    "flex.appt.button.attend": {
        "zh-TW": "我已到診",
        "en": "I've arrived",
        "id": "Saya sudah tiba",
        "vi": "Tôi đã đến nơi",
        "th": "มาถึงแล้ว",
        "ja": "到着しました",
    },
    "flex.appt.header.pre": {
        "zh-TW": "門診提醒",
        "en": "Appointment reminder",
        "id": "Pengingat jadwal periksa",
        "vi": "Nhắc lịch khám",
        "th": "แจ้งเตือนนัดหมายแพทย์",
        "ja": "受診のお知らせ",
    },
    "flex.appt.pre_body.self": {
        "zh-TW": "出發時請按下方的「我已出發」。",
        "en": "Tap “I'm on my way” below when you leave.",
        "id": "Ketuk “Saya sudah berangkat” di bawah saat Anda berangkat.",
        "vi": "Hãy bấm “Tôi đã xuất phát” bên dưới khi bạn rời nhà.",
        "th": "เมื่อออกเดินทาง กด “ออกเดินทางแล้ว” ด้านล่าง",
        "ja": "出発したら、下の「出発しました」を押してください。",
    },
    "flex.appt.pre_body.family": {
        "zh-TW": "{name} 出發時，您或{name}本人都可以按下方的「我已出發」。",
        "en": "When {name} leaves, either you or {name} can tap “I'm on my way” below.",
        "id": "Saat {name} berangkat, Anda atau {name} dapat mengetuk “Saya sudah berangkat” di bawah.",
        "vi": "Khi {name} xuất phát, bạn hoặc {name} đều có thể bấm “Tôi đã xuất phát” bên dưới.",
        "th": "เมื่อ {name} ออกเดินทาง คุณหรือ {name} กด “ออกเดินทางแล้ว” ด้านล่างได้",
        "ja": "{name} さんが出発したら、あなたか {name} さんが下の「出発しました」を押してください。",
    },
    "flex.appt.header.start": {
        "zh-TW": "門診時間到了",
        "en": "It's appointment time",
        "id": "Sudah waktunya periksa",
        "vi": "Đã đến giờ khám",
        "th": "ถึงเวลานัดแล้ว",
        "ja": "受診の時間です",
    },
    "flex.appt.start_body.self": {
        "zh-TW": "到了之後請按下方的「我已到診」，後續的提醒就會停止。",
        "en": "Once you arrive, tap “I've arrived” below and the remaining reminders will stop.",
        "id": "Setelah tiba, ketuk “Saya sudah tiba” di bawah dan pengingat berikutnya akan berhenti.",
        "vi": "Khi đến nơi, hãy bấm “Tôi đã đến nơi” bên dưới, các lời nhắc sau đó sẽ dừng.",
        "th": "เมื่อถึงแล้ว กด “มาถึงแล้ว” ด้านล่าง การแจ้งเตือนที่เหลือจะหยุด",
        "ja": "着いたら下の「到着しました」を押してください。以降のお知らせは止まります。",
    },
    "flex.appt.start_body.family": {
        "zh-TW": "{name} 到了之後，您或{name}本人都可以按下方的「我已到診」。",
        "en": "Once {name} arrives, either you or {name} can tap “I've arrived” below.",
        "id": "Setelah {name} tiba, Anda atau {name} dapat mengetuk “Saya sudah tiba” di bawah.",
        "vi": "Khi {name} đến nơi, bạn hoặc {name} đều có thể bấm “Tôi đã đến nơi” bên dưới.",
        "th": "เมื่อ {name} มาถึง คุณหรือ {name} กด “มาถึงแล้ว” ด้านล่างได้",
        "ja": "{name} さんが着いたら、あなたか {name} さんが下の「到着しました」を押してください。",
    },
    "flex.appt.header.not_departed": {
        "zh-TW": "還沒出發嗎？",
        "en": "Haven't left yet?",
        "id": "Belum berangkat?",
        "vi": "Chưa xuất phát sao?",
        "th": "ยังไม่ได้ออกเดินทางหรือ?",
        "ja": "まだ出発していませんか？",
    },
    "flex.appt.not_departed_body.self": {
        "zh-TW": "門診時間已經到了，還沒有收到「我已出發」的回報。如果已經到了，請直接按「我已到診」。",
        "en": "It's appointment time and we haven't received “I'm on my way” yet. If you're already there, tap “I've arrived”.",
        "id": "Sudah waktunya periksa, tetapi belum ada konfirmasi “Saya sudah berangkat”. Jika sudah tiba, langsung ketuk “Saya sudah tiba”.",
        "vi": "Đã đến giờ khám nhưng chưa nhận được xác nhận “Tôi đã xuất phát”. Nếu bạn đã đến nơi, hãy bấm “Tôi đã đến nơi”.",
        "th": "ถึงเวลานัดแล้ว แต่ยังไม่ได้รับการยืนยัน “ออกเดินทางแล้ว” หากถึงแล้ว กด “มาถึงแล้ว” ได้เลย",
        "ja": "受診の時間になりましたが、「出発しました」がまだ押されていません。すでに着いている場合は「到着しました」を押してください。",
    },
    "flex.appt.not_departed_body.family": {
        "zh-TW": "門診時間已經到了，{name} 還沒有回報出發。如果已經到了，您或{name}本人都可以直接按「我已到診」。",
        "en": "It's appointment time and {name} hasn't reported leaving yet. If they're already there, either of you can tap “I've arrived”.",
        "id": "Sudah waktunya periksa, tetapi {name} belum melapor berangkat. Jika sudah tiba, Anda atau {name} dapat langsung mengetuk “Saya sudah tiba”.",
        "vi": "Đã đến giờ khám nhưng {name} chưa báo đã xuất phát. Nếu đã đến nơi, bạn hoặc {name} đều có thể bấm “Tôi đã đến nơi”.",
        "th": "ถึงเวลานัดแล้ว แต่ {name} ยังไม่ได้แจ้งว่าออกเดินทาง หากถึงแล้ว คุณหรือ {name} กด “มาถึงแล้ว” ได้เลย",
        "ja": "受診の時間になりましたが、{name} さんからまだ出発の連絡がありません。すでに着いている場合は、あなたか {name} さんが「到着しました」を押してください。",
    },
    "flex.appt.header.caregiver": {
        "zh-TW": "尚未確認到診",
        "en": "Arrival not confirmed",
        "id": "Kedatangan belum dikonfirmasi",
        "vi": "Chưa xác nhận đã đến",
        "th": "ยังไม่ยืนยันการมาถึง",
        "ja": "到着が未確認です",
    },
    "flex.appt.caregiver_body.departed": {
        "zh-TW": "{name} 已在 {time} 回報出發，但門診開始 30 分鐘後仍未確認到診，可能在路上遇到狀況，請聯絡關心。",
        "en": "{name} reported leaving at {time}, but arrival still isn't confirmed 30 minutes after the appointment time. Something may have happened on the way — please check in.",
        "id": "{name} melapor berangkat pukul {time}, tetapi 30 menit setelah jadwal periksa kedatangan belum dikonfirmasi. Mungkin ada kendala di jalan — mohon hubungi.",
        "vi": "{name} đã báo xuất phát lúc {time}, nhưng 30 phút sau giờ khám vẫn chưa xác nhận đã đến. Có thể đã gặp chuyện trên đường — hãy liên lạc hỏi thăm.",
        "th": "{name} แจ้งว่าออกเดินทางเมื่อ {time} แต่ผ่านเวลานัดไป 30 นาทีแล้วยังไม่ยืนยันว่ามาถึง อาจเกิดเหตุระหว่างทาง โปรดติดต่อสอบถาม",
        "ja": "{name} さんは {time} に出発の連絡がありましたが、受診時刻から 30 分たっても到着が確認できません。途中で何かあったかもしれません。連絡してみてください。",
    },
    "flex.appt.caregiver_body.not_departed": {
        "zh-TW": "門診開始 30 分鐘後，{name} 仍未回報出發或到診，請聯絡確認。",
        "en": "30 minutes after the appointment time, {name} still hasn't reported leaving or arriving. Please check in.",
        "id": "30 menit setelah jadwal periksa, {name} belum melapor berangkat maupun tiba. Mohon hubungi untuk memastikan.",
        "vi": "30 phút sau giờ khám, {name} vẫn chưa báo xuất phát hay đã đến. Hãy liên lạc để xác nhận.",
        "th": "ผ่านเวลานัดไป 30 นาทีแล้ว {name} ยังไม่ได้แจ้งว่าออกเดินทางหรือมาถึง โปรดติดต่อสอบถาม",
        "ja": "受診時刻から 30 分たっても、{name} さんから出発・到着の連絡がありません。確認してみてください。",
    },
    "flex.appt.header.departed_done": {
        "zh-TW": "已記錄出發",
        "en": "Departure recorded",
        "id": "Keberangkatan tercatat",
        "vi": "Đã ghi nhận xuất phát",
        "th": "บันทึกการออกเดินทางแล้ว",
        "ja": "出発を記録しました",
    },
    "flex.appt.header.attended_done": {
        "zh-TW": "已記錄到診",
        "en": "Arrival recorded",
        "id": "Kedatangan tercatat",
        "vi": "Đã ghi nhận đã đến",
        "th": "บันทึกการมาถึงแล้ว",
        "ja": "到着を記録しました",
    },
    "flex.appt.reported_by": {
        "zh-TW": "{time}　由 {name} 回報",
        "en": "Reported by {name} at {time}",
        "id": "Dilaporkan oleh {name} pukul {time}",
        "vi": "{name} đã báo lúc {time}",
        "th": "{name} แจ้งเมื่อ {time}",
        "ja": "{time}　{name}が連絡しました",
    },
    "flex.appt.departed_done_hint": {
        "zh-TW": "到了之後記得按「我已到診」。",
        "en": "Remember to tap “I've arrived” when you get there.",
        "id": "Jangan lupa ketuk “Saya sudah tiba” setelah sampai.",
        "vi": "Nhớ bấm “Tôi đã đến nơi” khi đến.",
        "th": "เมื่อถึงแล้ว อย่าลืมกด “มาถึงแล้ว”",
        "ja": "着いたら「到着しました」を押してください。",
    },
    "flex.appt.attended_done_hint": {
        "zh-TW": "這次門診後續的提醒已全部停止。",
        "en": "All remaining reminders for this appointment have stopped.",
        "id": "Semua pengingat berikutnya untuk jadwal ini telah dihentikan.",
        "vi": "Mọi lời nhắc còn lại cho lịch khám này đã dừng.",
        "th": "การแจ้งเตือนที่เหลือของนัดนี้หยุดแล้วทั้งหมด",
        "ja": "この受診の残りのお知らせはすべて停止しました。",
    },
    "flex.appt.alt.family_prefix": {
        "zh-TW": "【{name}】",
        "en": "[{name}] ",
        "id": "[{name}] ",
        "vi": "[{name}] ",
        "th": "[{name}] ",
        "ja": "【{name}】",
    },
    "flex.appt.alt.pre": {
        "zh-TW": "門診提醒：{when} {hospital}",
        "en": "Appointment reminder: {when}, {hospital}",
        "id": "Pengingat jadwal periksa: {when}, {hospital}",
        "vi": "Nhắc lịch khám: {when}, {hospital}",
        "th": "แจ้งเตือนนัดหมาย: {when} {hospital}",
        "ja": "受診のお知らせ：{when} {hospital}",
    },
    "flex.appt.alt.start": {
        "zh-TW": "門診時間到了：{hospital}",
        "en": "It's appointment time: {hospital}",
        "id": "Sudah waktunya periksa: {hospital}",
        "vi": "Đã đến giờ khám: {hospital}",
        "th": "ถึงเวลานัดแล้ว: {hospital}",
        "ja": "受診の時間です：{hospital}",
    },
    "flex.appt.alt.not_departed": {
        "zh-TW": "門診時間到了，還沒出發嗎？{hospital}",
        "en": "It's appointment time — haven't left yet? {hospital}",
        "id": "Sudah waktunya periksa — belum berangkat? {hospital}",
        "vi": "Đã đến giờ khám — chưa xuất phát sao? {hospital}",
        "th": "ถึงเวลานัดแล้ว ยังไม่ได้ออกเดินทางหรือ? {hospital}",
        "ja": "受診の時間です。まだ出発していませんか？{hospital}",
    },
    "flex.appt.alt.caregiver": {
        "zh-TW": "{name} 的門診尚未確認到診",
        "en": "{name}'s arrival at the appointment isn't confirmed",
        "id": "Kedatangan {name} di jadwal periksa belum dikonfirmasi",
        "vi": "Chưa xác nhận {name} đã đến buổi khám",
        "th": "ยังไม่ยืนยันว่า {name} มาถึงนัดหมาย",
        "ja": "{name} さんの受診の到着が未確認です",
    },
    "flex.appt.alt.departed_done": {
        "zh-TW": "已記錄出發：{hospital}",
        "en": "Departure recorded: {hospital}",
        "id": "Keberangkatan tercatat: {hospital}",
        "vi": "Đã ghi nhận xuất phát: {hospital}",
        "th": "บันทึกการออกเดินทางแล้ว: {hospital}",
        "ja": "出発を記録しました：{hospital}",
    },
    "flex.appt.alt.attended_done": {
        "zh-TW": "已記錄到診：{hospital}",
        "en": "Arrival recorded: {hospital}",
        "id": "Kedatangan tercatat: {hospital}",
        "vi": "Đã ghi nhận đã đến: {hospital}",
        "th": "บันทึกการมาถึงแล้ว: {hospital}",
        "ja": "到着を記録しました：{hospital}",
    },
    # 出發／到診失敗的回覆。繁中版同時是 API 的 detail（前端原樣顯示），
    # 其餘語言只用在 LINE 卡片按鈕的回覆。
    "appt.error.not_found": {
        "zh-TW": "找不到這筆掛號提醒，可能已經被刪除。",
        "en": "This appointment reminder wasn't found. It may have been deleted.",
        "id": "Pengingat jadwal periksa ini tidak ditemukan. Mungkin sudah dihapus.",
        "vi": "Không tìm thấy lời nhắc lịch khám này. Có thể đã bị xóa.",
        "th": "ไม่พบการแจ้งเตือนนัดหมายนี้ อาจถูกลบไปแล้ว",
        "ja": "この受診のお知らせが見つかりません。削除された可能性があります。",
    },
    "appt.error.forbidden_report": {
        "zh-TW": "您沒有權限替這位家人回報出發或到診。",
        "en": "You don't have permission to report departure or arrival for this person.",
        "id": "Anda tidak memiliki izin untuk melaporkan keberangkatan atau kedatangan orang ini.",
        "vi": "Bạn không có quyền báo xuất phát hoặc đã đến thay cho người này.",
        "th": "คุณไม่มีสิทธิ์แจ้งการออกเดินทางหรือการมาถึงแทนบุคคลนี้",
        "ja": "この方の出発・到着を連絡する権限がありません。",
    },
    "appt.error.depart_after_attend": {
        "zh-TW": "已經回報到診了，不需要再回報出發。",
        "en": "Arrival has already been reported, so there's no need to report departure.",
        "id": "Kedatangan sudah dilaporkan, tidak perlu melapor berangkat lagi.",
        "vi": "Đã báo đến nơi rồi, không cần báo xuất phát nữa.",
        "th": "แจ้งว่ามาถึงแล้ว ไม่ต้องแจ้งออกเดินทางอีก",
        "ja": "すでに到着の連絡があるため、出発の連絡は不要です。",
    },
    "appt.error.missed": {
        "zh-TW": "這個門診的當天已經結束，無法再回報出發或到診。",
        "en": "The day of this appointment has ended, so departure or arrival can no longer be reported.",
        "id": "Hari jadwal periksa ini sudah berakhir, keberangkatan atau kedatangan tidak bisa dilaporkan lagi.",
        "vi": "Ngày của buổi khám này đã qua, không thể báo xuất phát hoặc đã đến nữa.",
        "th": "วันนัดหมายนี้ผ่านไปแล้ว ไม่สามารถแจ้งการออกเดินทางหรือการมาถึงได้อีก",
        "ja": "この受診日は終了したため、出発・到着の連絡はできません。",
    },
    "appt.error.cancelled": {
        "zh-TW": "這筆掛號提醒已經取消，無法回報出發或到診。",
        "en": "This appointment reminder has been cancelled, so departure or arrival can't be reported.",
        "id": "Pengingat jadwal periksa ini sudah dibatalkan, keberangkatan atau kedatangan tidak bisa dilaporkan.",
        "vi": "Lời nhắc lịch khám này đã bị hủy, không thể báo xuất phát hoặc đã đến.",
        "th": "การแจ้งเตือนนัดหมายนี้ถูกยกเลิกแล้ว ไม่สามารถแจ้งการออกเดินทางหรือการมาถึงได้",
        "ja": "この受診のお知らせは取り消されたため、出発・到着の連絡はできません。",
    },
    "appt.error.too_early": {
        "zh-TW": "門診當天才能回報出發或到診。",
        "en": "Departure and arrival can only be reported on the day of the appointment.",
        "id": "Keberangkatan dan kedatangan hanya bisa dilaporkan pada hari jadwal periksa.",
        "vi": "Chỉ có thể báo xuất phát hoặc đã đến vào ngày khám.",
        "th": "แจ้งการออกเดินทางหรือการมาถึงได้เฉพาะในวันนัดเท่านั้น",
        "ja": "出発・到着の連絡は受診日当日にのみできます。",
    },
    # --- 星期 ---
    "weekday.monday": {
        "zh-TW": "週一", "en": "Mon", "id": "Sen",
        "vi": "T2", "th": "จ.", "ja": "月",
    },
    "weekday.tuesday": {
        "zh-TW": "週二", "en": "Tue", "id": "Sel",
        "vi": "T3", "th": "อ.", "ja": "火",
    },
    "weekday.wednesday": {
        "zh-TW": "週三", "en": "Wed", "id": "Rab",
        "vi": "T4", "th": "พ.", "ja": "水",
    },
    "weekday.thursday": {
        "zh-TW": "週四", "en": "Thu", "id": "Kam",
        "vi": "T5", "th": "พฤ.", "ja": "木",
    },
    "weekday.friday": {
        "zh-TW": "週五", "en": "Fri", "id": "Jum",
        "vi": "T6", "th": "ศ.", "ja": "金",
    },
    "weekday.saturday": {
        "zh-TW": "週六", "en": "Sat", "id": "Sab",
        "vi": "T7", "th": "ส.", "ja": "土",
    },
    "weekday.sunday": {
        "zh-TW": "週日", "en": "Sun", "id": "Min",
        "vi": "CN", "th": "อา.", "ja": "日",
    },
    # --- 星期（門診表欄頭專用的極短版）---
    "weekday.short.monday": {
        "zh-TW": "一", "en": "Mo", "id": "Sn",
        "vi": "T2", "th": "จ", "ja": "月",
    },
    "weekday.short.tuesday": {
        "zh-TW": "二", "en": "Tu", "id": "Sl",
        "vi": "T3", "th": "อ", "ja": "火",
    },
    "weekday.short.wednesday": {
        "zh-TW": "三", "en": "We", "id": "Rb",
        "vi": "T4", "th": "พ", "ja": "水",
    },
    "weekday.short.thursday": {
        "zh-TW": "四", "en": "Th", "id": "Km",
        "vi": "T5", "th": "พฤ", "ja": "木",
    },
    "weekday.short.friday": {
        "zh-TW": "五", "en": "Fr", "id": "Jm",
        "vi": "T6", "th": "ศ", "ja": "金",
    },
    "weekday.short.saturday": {
        "zh-TW": "六", "en": "Sa", "id": "Sb",
        "vi": "T7", "th": "ส", "ja": "土",
    },
    "weekday.short.sunday": {
        "zh-TW": "日", "en": "Su", "id": "Mg",
        "vi": "CN", "th": "อา", "ja": "日",
    },
    # --- 診療科別 ---
    # key 直接用資料庫的中文原文（medicalFacilities.departments 的 distinct 值），
    # 因為那本來就是唯一穩定的識別字；另外造一組英文 slug 只會多一層對照表要維護。
    # 查不到的科別（次專科、或整串塞在一格的髒資料）由 department_label() 原樣回傳，
    # 絕不會顯示成 "department.XXX" 這種 key。
    "department.內科": {
        "zh-TW": "內科", "en": "Internal Medicine", "id": "Penyakit Dalam",
        "vi": "Nội khoa", "th": "อายุรกรรม", "ja": "内科",
    },
    "department.外科": {
        "zh-TW": "外科", "en": "Surgery", "id": "Bedah",
        "vi": "Ngoại khoa", "th": "ศัลยกรรม", "ja": "外科",
    },
    "department.兒科": {
        "zh-TW": "兒科", "en": "Pediatrics", "id": "Anak",
        "vi": "Nhi khoa", "th": "กุมารเวชกรรม", "ja": "小児科",
    },
    "department.婦產科": {
        "zh-TW": "婦產科", "en": "Obstetrics & Gynecology", "id": "Kebidanan & Kandungan",
        "vi": "Sản phụ khoa", "th": "สูตินรีเวช", "ja": "産婦人科",
    },
    "department.骨科": {
        "zh-TW": "骨科", "en": "Orthopedics", "id": "Ortopedi",
        "vi": "Chấn thương chỉnh hình", "th": "ศัลยกรรมกระดูก", "ja": "整形外科",
    },
    "department.神經科": {
        "zh-TW": "神經科", "en": "Neurology", "id": "Saraf",
        "vi": "Thần kinh", "th": "ประสาทวิทยา", "ja": "神経内科",
    },
    "department.神經外科": {
        "zh-TW": "神經外科", "en": "Neurosurgery", "id": "Bedah Saraf",
        "vi": "Ngoại thần kinh", "th": "ศัลยกรรมประสาท", "ja": "脳神経外科",
    },
    "department.泌尿科": {
        "zh-TW": "泌尿科", "en": "Urology", "id": "Urologi",
        "vi": "Tiết niệu", "th": "ระบบทางเดินปัสสาวะ", "ja": "泌尿器科",
    },
    "department.耳鼻喉科": {
        "zh-TW": "耳鼻喉科", "en": "ENT", "id": "THT",
        "vi": "Tai mũi họng", "th": "หู คอ จมูก", "ja": "耳鼻咽喉科",
    },
    "department.眼科": {
        "zh-TW": "眼科", "en": "Ophthalmology", "id": "Mata",
        "vi": "Nhãn khoa", "th": "จักษุวิทยา", "ja": "眼科",
    },
    "department.皮膚科": {
        "zh-TW": "皮膚科", "en": "Dermatology", "id": "Kulit",
        "vi": "Da liễu", "th": "ผิวหนัง", "ja": "皮膚科",
    },
    "department.精神科": {
        "zh-TW": "精神科", "en": "Psychiatry", "id": "Psikiatri",
        "vi": "Tâm thần", "th": "จิตเวช", "ja": "精神科",
    },
    "department.復健科": {
        "zh-TW": "復健科", "en": "Rehabilitation", "id": "Rehabilitasi",
        "vi": "Phục hồi chức năng", "th": "เวชศาสตร์ฟื้นฟู", "ja": "リハビリテーション科",
    },
    "department.麻醉科": {
        "zh-TW": "麻醉科", "en": "Anesthesiology", "id": "Anestesi",
        "vi": "Gây mê", "th": "วิสัญญีวิทยา", "ja": "麻酔科",
    },
    "department.放射診斷科": {
        "zh-TW": "放射診斷科", "en": "Diagnostic Radiology", "id": "Radiologi Diagnostik",
        "vi": "Chẩn đoán hình ảnh", "th": "รังสีวินิจฉัย", "ja": "放射線診断科",
    },
    "department.放射腫瘤科": {
        "zh-TW": "放射腫瘤科", "en": "Radiation Oncology", "id": "Onkologi Radiasi",
        "vi": "Xạ trị ung thư", "th": "รังสีรักษา", "ja": "放射線腫瘍科",
    },
    "department.放射線科": {
        "zh-TW": "放射線科", "en": "Radiology", "id": "Radiologi",
        "vi": "Chẩn đoán hình ảnh", "th": "รังสีวิทยา", "ja": "放射線科",
    },
    "department.解剖病理科": {
        "zh-TW": "解剖病理科", "en": "Anatomical Pathology", "id": "Patologi Anatomi",
        "vi": "Giải phẫu bệnh", "th": "พยาธิกายวิภาค", "ja": "解剖病理科",
    },
    "department.臨床病理科": {
        "zh-TW": "臨床病理科", "en": "Clinical Pathology", "id": "Patologi Klinik",
        "vi": "Bệnh học lâm sàng", "th": "พยาธิคลินิก", "ja": "臨床病理科",
    },
    "department.病理科": {
        "zh-TW": "病理科", "en": "Pathology", "id": "Patologi",
        "vi": "Giải phẫu bệnh", "th": "พยาธิวิทยา", "ja": "病理科",
    },
    "department.核子醫學科": {
        "zh-TW": "核子醫學科", "en": "Nuclear Medicine", "id": "Kedokteran Nuklir",
        "vi": "Y học hạt nhân", "th": "เวชศาสตร์นิวเคลียร์", "ja": "核医学科",
    },
    "department.急診醫學科": {
        "zh-TW": "急診醫學科", "en": "Emergency Medicine", "id": "Gawat Darurat",
        "vi": "Cấp cứu", "th": "เวชศาสตร์ฉุกเฉิน", "ja": "救急科",
    },
    "department.整形外科": {
        "zh-TW": "整形外科", "en": "Plastic Surgery", "id": "Bedah Plastik",
        "vi": "Phẫu thuật tạo hình", "th": "ศัลยกรรมตกแต่ง", "ja": "形成外科",
    },
    "department.職業醫學科": {
        "zh-TW": "職業醫學科", "en": "Occupational Medicine", "id": "Kedokteran Okupasi",
        "vi": "Y học lao động", "th": "อาชีวเวชศาสตร์", "ja": "産業医学科",
    },
    "department.家庭醫學科": {
        "zh-TW": "家庭醫學科", "en": "Family Medicine", "id": "Kedokteran Keluarga",
        "vi": "Y học gia đình", "th": "เวชศาสตร์ครอบครัว", "ja": "家庭医学科",
    },
    "department.家醫科": {
        "zh-TW": "家醫科", "en": "Family Medicine", "id": "Kedokteran Keluarga",
        "vi": "Y học gia đình", "th": "เวชศาสตร์ครอบครัว", "ja": "家庭医学科",
    },
    "department.西醫一般科": {
        "zh-TW": "西醫一般科", "en": "General Medicine", "id": "Kedokteran Umum",
        "vi": "Tây y tổng quát", "th": "แพทย์ทั่วไป", "ja": "一般内科",
    },
    "department.不分科": {
        "zh-TW": "不分科", "en": "General (Unspecified)", "id": "Umum",
        "vi": "Không phân khoa", "th": "ไม่ระบุแผนก", "ja": "総合診療",
    },
    "department.中醫一般科": {
        "zh-TW": "中醫一般科", "en": "Traditional Chinese Medicine", "id": "Pengobatan Tradisional Tiongkok",
        "vi": "Đông y", "th": "แพทย์แผนจีน", "ja": "中医一般",
    },
    "department.洗腎科": {
        "zh-TW": "洗腎科", "en": "Dialysis", "id": "Dialisis",
        "vi": "Lọc máu", "th": "ฟอกไต", "ja": "透析科",
    },
    "department.一般精神科": {
        "zh-TW": "一般精神科", "en": "General Psychiatry", "id": "Psikiatri Umum",
        "vi": "Tâm thần tổng quát", "th": "จิตเวชทั่วไป", "ja": "一般精神科",
    },
    "department.兒童青少年精神科": {
        "zh-TW": "兒童青少年精神科", "en": "Child & Adolescent Psychiatry",
        "id": "Psikiatri Anak & Remaja", "vi": "Tâm thần trẻ em & vị thành niên",
        "th": "จิตเวชเด็กและวัยรุ่น", "ja": "児童思春期精神科",
    },
    "department.成癮防治科": {
        "zh-TW": "成癮防治科", "en": "Addiction Medicine", "id": "Kedokteran Adiksi",
        "vi": "Cai nghiện", "th": "เวชศาสตร์การเสพติด", "ja": "依存症治療科",
    },
    "department.牙科": {
        "zh-TW": "牙科", "en": "Dentistry", "id": "Kedokteran Gigi",
        "vi": "Nha khoa", "th": "ทันตกรรม", "ja": "歯科",
    },
    "department.牙醫一般科": {
        "zh-TW": "牙醫一般科", "en": "General Dentistry", "id": "Kedokteran Gigi Umum",
        "vi": "Nha khoa tổng quát", "th": "ทันตกรรมทั่วไป", "ja": "一般歯科",
    },
    "department.家庭牙醫科": {
        "zh-TW": "家庭牙醫科", "en": "Family Dentistry", "id": "Kedokteran Gigi Keluarga",
        "vi": "Nha khoa gia đình", "th": "ทันตกรรมครอบครัว", "ja": "家庭歯科",
    },
    "department.兒童牙科": {
        "zh-TW": "兒童牙科", "en": "Pediatric Dentistry", "id": "Kedokteran Gigi Anak",
        "vi": "Nha khoa trẻ em", "th": "ทันตกรรมเด็ก", "ja": "小児歯科",
    },
    "department.齒顎矯正科": {
        "zh-TW": "齒顎矯正科", "en": "Orthodontics", "id": "Ortodonsia",
        "vi": "Chỉnh nha", "th": "ทันตกรรมจัดฟัน", "ja": "矯正歯科",
    },
    "department.牙周病科": {
        "zh-TW": "牙周病科", "en": "Periodontics", "id": "Periodonsia",
        "vi": "Nha chu", "th": "ปริทันตวิทยา", "ja": "歯周病科",
    },
    "department.牙髓病科": {
        "zh-TW": "牙髓病科", "en": "Endodontics", "id": "Endodonsia",
        "vi": "Nội nha", "th": "วิทยาเอ็นโดดอนต์", "ja": "歯内療法科",
    },
    "department.贋復補綴牙科": {
        "zh-TW": "贋復補綴牙科", "en": "Prosthodontics", "id": "Prostodonsia",
        "vi": "Phục hình răng", "th": "ทันตกรรมประดิษฐ์", "ja": "補綴歯科",
    },
    "department.口腔顎面外科": {
        "zh-TW": "口腔顎面外科", "en": "Oral & Maxillofacial Surgery",
        "id": "Bedah Mulut & Maksilofasial", "vi": "Phẫu thuật hàm mặt",
        "th": "ศัลยกรรมช่องปาก", "ja": "口腔外科",
    },
    "department.特殊需求者口腔醫學科": {
        "zh-TW": "特殊需求者口腔醫學科", "en": "Special Needs Dentistry",
        "id": "Kedokteran Gigi Kebutuhan Khusus", "vi": "Nha khoa nhu cầu đặc biệt",
        "th": "ทันตกรรมผู้มีความต้องการพิเศษ", "ja": "障害者歯科",
    },
    # --- 用藥時段 ---
    "slot.morning": {
        "zh-TW": "早", "en": "Morning", "id": "Pagi",
        "vi": "Sáng", "th": "เช้า", "ja": "朝",
    },
    "slot.noon": {
        "zh-TW": "中", "en": "Noon", "id": "Siang",
        "vi": "Trưa", "th": "กลางวัน", "ja": "昼",
    },
    "slot.evening": {
        "zh-TW": "晚", "en": "Evening", "id": "Malam",
        "vi": "Tối", "th": "เย็น", "ja": "夕",
    },
    "slot.bedtime": {
        "zh-TW": "睡前", "en": "Bedtime", "id": "Sebelum tidur",
        "vi": "Trước khi ngủ", "th": "ก่อนนอน", "ja": "就寝前",
    },
    # --- 服藥時機（規則內的條目，design 決策 6：飯前 → 飯後 → 其他固定順序）---
    "meal.before_meal": {
        "zh-TW": "飯前", "en": "Before meal", "id": "Sebelum makan",
        "vi": "Trước ăn", "th": "ก่อนอาหาร", "ja": "食前",
    },
    "meal.after_meal": {
        "zh-TW": "飯後", "en": "After meal", "id": "Sesudah makan",
        "vi": "Sau ăn", "th": "หลังอาหาร", "ja": "食後",
    },
    "meal.none": {
        "zh-TW": "其他", "en": "Other", "id": "Lainnya",
        "vi": "Khác", "th": "อื่น ๆ", "ja": "その他",
    },
    # --- 查服藥狀況（直通給使用者、不經模型改寫，見 medication_status_service）---
    # 用詞只講資料證明得了的事：系統只知道有沒有按下【已服用】，所以是「已確認
    # 服用」「逾時未確認」，不是「吃了」「漏吃」。
    "medstatus.header.day_self": {
        "zh-TW": "您{day}的用藥", "en": "Your medicines {day}", "id": "Obat Anda {day}",
        "vi": "Thuốc của bạn {day}", "th": "ยาของคุณ {day}", "ja": "{day}のお薬",
    },
    "medstatus.header.day_other": {
        "zh-TW": "{name}{day}的用藥", "en": "{name}'s medicines {day}",
        "id": "Obat {name} {day}", "vi": "Thuốc của {name} {day}",
        "th": "ยาของ {name} {day}", "ja": "{name}さんの{day}のお薬",
    },
    "medstatus.header.range_self": {
        "zh-TW": "您最近 {n} 天的服藥紀錄",
        "en": "Your medicine record for the last {n} days",
        "id": "Catatan obat Anda {n} hari terakhir",
        "vi": "Lịch sử uống thuốc của bạn trong {n} ngày gần đây",
        "th": "บันทึกการกินยาของคุณ {n} วันล่าสุด",
        "ja": "直近{n}日間のお薬の記録",
    },
    "medstatus.header.range_other": {
        "zh-TW": "{name}最近 {n} 天的服藥紀錄",
        "en": "{name}'s medicine record for the last {n} days",
        "id": "Catatan obat {name} {n} hari terakhir",
        "vi": "Lịch sử uống thuốc của {name} trong {n} ngày gần đây",
        "th": "บันทึกการกินยาของ {name} {n} วันล่าสุด",
        "ja": "{name}さんの直近{n}日間のお薬の記録",
    },
    "medstatus.day.today": {
        "zh-TW": "今天（{date}）", "en": "today ({date})", "id": "hari ini ({date})",
        "vi": "hôm nay ({date})", "th": "วันนี้ ({date})", "ja": "今日（{date}）",
    },
    "medstatus.day.yesterday": {
        "zh-TW": "昨天（{date}）", "en": "yesterday ({date})", "id": "kemarin ({date})",
        "vi": "hôm qua ({date})", "th": "เมื่อวาน ({date})", "ja": "昨日（{date}）",
    },
    # 中文日期前後留空格，「您 9/12 的用藥」才不會黏成一串。
    "medstatus.day.other": {
        "zh-TW": " {date} ", "en": "on {date}", "id": "tanggal {date}",
        "vi": "ngày {date}", "th": "วันที่ {date}", "ja": "{date}",
    },
    # 印尼、越南、泰國慣用日在前；「9/10」在那裡會被讀成十月九日。
    "medstatus.date": {
        "zh-TW": "{m}/{d}", "en": "{m}/{d}", "id": "{d}/{m}",
        "vi": "{d}/{m}", "th": "{d}/{m}", "ja": "{m}/{d}",
    },
    "medstatus.slot_line": {
        "zh-TW": "{slot} {time}　{state}", "en": "{slot} {time} – {state}",
        "id": "{slot} {time} – {state}", "vi": "{slot} {time} – {state}",
        "th": "{slot} {time} – {state}", "ja": "{slot} {time}　{state}",
    },
    "medstatus.med_with_timing": {
        "zh-TW": "{name}（{meal} {time}）", "en": "{name} ({meal} {time})",
        "id": "{name} ({meal} {time})", "vi": "{name} ({meal} {time})",
        "th": "{name} ({meal} {time})", "ja": "{name}（{meal} {time}）",
    },
    "medstatus.state.taken_at": {
        "zh-TW": "已確認服用（{time}）", "en": "Taken, confirmed at {time}",
        "id": "Sudah dikonfirmasi diminum ({time})", "vi": "Đã xác nhận uống ({time})",
        "th": "ยืนยันว่ากินแล้ว ({time})", "ja": "服用を確認済み（{time}）",
    },
    "medstatus.state.taken": {
        "zh-TW": "已確認服用", "en": "Taken, confirmed",
        "id": "Sudah dikonfirmasi diminum", "vi": "Đã xác nhận uống",
        "th": "ยืนยันว่ากินแล้ว", "ja": "服用を確認済み",
    },
    "medstatus.state.pending": {
        "zh-TW": "還沒確認", "en": "Not confirmed yet", "id": "Belum dikonfirmasi",
        "vi": "Chưa xác nhận", "th": "ยังไม่ได้ยืนยัน", "ja": "まだ確認されていません",
    },
    "medstatus.state.missed": {
        "zh-TW": "逾時未確認", "en": "Not confirmed in time",
        "id": "Tidak dikonfirmasi tepat waktu", "vi": "Quá giờ chưa xác nhận",
        "th": "ไม่ได้ยืนยันภายในเวลา", "ja": "時間内に確認なし",
    },
    "medstatus.state.upcoming": {
        "zh-TW": "還沒到", "en": "Coming up", "id": "Belum waktunya",
        "vi": "Chưa đến giờ", "th": "ยังไม่ถึงเวลา", "ja": "まだ時間前",
    },
    "medstatus.summary.counts": {
        "zh-TW": "{date}：已確認 {taken}/{total}", "en": "{date}: {taken}/{total} confirmed",
        "id": "{date}: {taken}/{total} dikonfirmasi", "vi": "{date}: đã xác nhận {taken}/{total}",
        "th": "{date}: ยืนยันแล้ว {taken}/{total}", "ja": "{date}：確認済み {taken}/{total}",
    },
    "medstatus.summary.slots": {
        "zh-TW": "{state}：{slots}", "en": "{state}: {slots}", "id": "{state}: {slots}",
        "vi": "{state}: {slots}", "th": "{state}: {slots}", "ja": "{state}：{slots}",
    },
    "medstatus.summary.joiner": {
        "zh-TW": "，", "en": "; ", "id": "; ", "vi": "; ", "th": "; ", "ja": "、",
    },
    "medstatus.summary.none": {
        "zh-TW": "{date}：沒有紀錄", "en": "{date}: no record",
        "id": "{date}: tidak ada catatan", "vi": "{date}: không có dữ liệu",
        "th": "{date}: ไม่มีบันทึก", "ja": "{date}：記録なし",
    },
    "medstatus.list_sep": {
        "zh-TW": "、", "en": ", ", "id": ", ", "vi": ", ", "th": ", ", "ja": "、",
    },
    "medstatus.no_slots": {
        "zh-TW": "這天沒有要吃的藥。", "en": "No medicines are scheduled for this day.",
        "id": "Tidak ada obat yang dijadwalkan untuk hari ini.",
        "vi": "Không có thuốc cần uống trong ngày này.",
        "th": "วันนี้ไม่มียาที่ต้องกิน", "ja": "この日に飲むお薬はありません。",
    },
    "medstatus.no_record": {
        "zh-TW": "這天沒有紀錄。", "en": "There is no record for this day.",
        "id": "Tidak ada catatan untuk hari tersebut.", "vi": "Không có dữ liệu cho ngày này.",
        "th": "ไม่มีบันทึกของวันนั้น", "ja": "この日の記録はありません。",
    },
    "medstatus.clamped": {
        "zh-TW": "目前只能查最近 {n} 天，以下是最近 {n} 天的紀錄。",
        "en": "I can only look back {n} days for now. Here are the last {n} days.",
        "id": "Saat ini hanya bisa melihat {n} hari terakhir. Berikut catatan {n} hari terakhir.",
        "vi": "Hiện chỉ xem được {n} ngày gần đây. Dưới đây là {n} ngày gần đây.",
        "th": "ตอนนี้ดูย้อนหลังได้แค่ {n} วัน นี่คือบันทึก {n} วันล่าสุด",
        "ja": "現在は直近{n}日間のみ確認できます。以下は直近{n}日間の記録です。",
    },
    "medstatus.out_of_range": {
        "zh-TW": "目前只能查最近 {n} 天的紀錄。", "en": "I can only look back {n} days for now.",
        "id": "Saat ini hanya bisa melihat catatan {n} hari terakhir.",
        "vi": "Hiện chỉ xem được dữ liệu {n} ngày gần đây.",
        "th": "ตอนนี้ดูบันทึกย้อนหลังได้แค่ {n} วัน",
        "ja": "現在は直近{n}日間の記録のみ確認できます。",
    },
    "medstatus.no_reminders.self": {
        "zh-TW": "您目前沒有設定用藥提醒。要查家人的話，可以說他的名字或關係，例如「媽媽今天吃藥了嗎」。",
        "en": "You don't have any medication reminders set up. To check on a family member, "
              "say their name or how they are related to you, for example “Did Mom take her medicine today?”",
        "id": "Anda belum mengatur pengingat obat. Untuk mengecek anggota keluarga, sebutkan nama "
              "atau hubungannya, misalnya “Apakah Ibu sudah minum obat hari ini?”",
        "vi": "Bạn chưa cài đặt nhắc uống thuốc. Muốn xem cho người thân, hãy nói tên hoặc mối quan hệ, "
              "ví dụ “Hôm nay mẹ đã uống thuốc chưa?”",
        "th": "คุณยังไม่ได้ตั้งการแจ้งเตือนกินยา ถ้าต้องการดูของคนในครอบครัว ให้บอกชื่อหรือความสัมพันธ์ "
              "เช่น “วันนี้แม่กินยาหรือยัง”",
        "ja": "お薬のリマインダーが設定されていません。ご家族のことを確認するには、お名前か続柄を"
              "伝えてください。例：「母は今日お薬を飲みましたか」",
    },
    "medstatus.no_reminders.other": {
        "zh-TW": "{name}目前沒有設定用藥提醒。",
        "en": "{name} doesn't have any medication reminders set up.",
        "id": "{name} belum memiliki pengingat obat.",
        "vi": "{name} chưa được cài đặt nhắc uống thuốc.",
        "th": "{name} ยังไม่ได้ตั้งการแจ้งเตือนกินยา",
        "ja": "{name}さんにはお薬のリマインダーが設定されていません。",
    },
    "medstatus.ambiguous": {
        "zh-TW": "您的家人裡有好幾位符合：{names}。請問是哪一位？",
        "en": "More than one family member matches: {names}. Which one do you mean?",
        "id": "Ada lebih dari satu anggota keluarga yang cocok: {names}. Yang mana maksud Anda?",
        "vi": "Có nhiều người thân phù hợp: {names}. Bạn muốn hỏi ai?",
        "th": "มีคนในครอบครัวที่ตรงกันหลายคน: {names} หมายถึงคนไหน",
        "ja": "該当するご家族が複数います：{names}。どなたのことですか？",
    },
    "medstatus.not_found": {
        "zh-TW": "在您的家人名單裡找不到「{query}」。名單上有：{names}。可以直接說名字。",
        "en": "I couldn't find “{query}” in your family list. Your list has: {names}. "
              "You can just say their name.",
        "id": "“{query}” tidak ditemukan di daftar keluarga Anda. Daftar Anda: {names}. "
              "Anda bisa langsung menyebut namanya.",
        "vi": "Không tìm thấy “{query}” trong danh sách người thân của bạn. Danh sách gồm: {names}. "
              "Bạn có thể nói thẳng tên.",
        "th": "ไม่พบ “{query}” ในรายชื่อครอบครัวของคุณ ในรายชื่อมี: {names} บอกชื่อได้เลย",
        "ja": "ご家族の一覧に「{query}」が見つかりません。一覧にいるのは：{names}。お名前で伝えてください。",
    },
    "medstatus.no_family": {
        "zh-TW": "您的家人名單裡還沒有其他人，目前只能查您自己的用藥。",
        "en": "There's no one else in your family list yet, so for now you can only check your own medicines.",
        "id": "Belum ada orang lain di daftar keluarga Anda, jadi saat ini Anda hanya bisa mengecek obat Anda sendiri.",
        "vi": "Danh sách người thân của bạn chưa có ai khác, hiện chỉ xem được thuốc của chính bạn.",
        "th": "ยังไม่มีคนอื่นในรายชื่อครอบครัวของคุณ ตอนนี้ดูได้เฉพาะยาของคุณเอง",
        "ja": "ご家族の一覧にまだ誰もいないため、今はご自身のお薬のみ確認できます。",
    },
    "medstatus.no_permission": {
        "zh-TW": "您沒有查看{name}用藥的權限。",
        "en": "You don't have permission to view {name}'s medicines.",
        "id": "Anda tidak memiliki izin untuk melihat obat {name}.",
        "vi": "Bạn không có quyền xem thuốc của {name}.",
        "th": "คุณไม่มีสิทธิ์ดูยาของ {name}",
        "ja": "{name}さんのお薬を見る権限がありません。",
    },
    "medstatus.error": {
        "zh-TW": "暫時查不到用藥紀錄，請稍後再試。",
        "en": "I can't look up the medicine record right now. Please try again later.",
        "id": "Catatan obat tidak dapat dilihat saat ini. Silakan coba lagi nanti.",
        "vi": "Hiện không tra được dữ liệu thuốc. Vui lòng thử lại sau.",
        "th": "ตอนนี้ยังดูบันทึกยาไม่ได้ กรุณาลองใหม่ภายหลัง",
        "ja": "現在お薬の記録を確認できません。しばらくしてからもう一度お試しください。",
    },
    "medstatus.unnamed": {
        "zh-TW": "未設定名字的家人", "en": "a family member with no name set",
        "id": "anggota keluarga tanpa nama", "vi": "người thân chưa đặt tên",
        "th": "คนในครอบครัวที่ยังไม่ได้ตั้งชื่อ", "ja": "名前未設定のご家族",
    },
    # --- Flex：用藥提醒 ---
    "flex.med.alt.reminder": {
        "zh-TW": "CARE 用藥提醒：{slot} 服藥時間到了",
        "en": "CARE reminder: time for your {slot} dose",
        "id": "Pengingat CARE: waktunya minum obat {slot}",
        "vi": "Nhắc nhở CARE: đã đến giờ uống thuốc {slot}",
        "th": "การแจ้งเตือน CARE: ถึงเวลายามื้อ{slot}แล้ว",
        "ja": "CARE 服薬リマインダー：{slot}の服薬時間です",
    },
    "flex.med.alt.done": {
        "zh-TW": "已完成 {slot} 用藥",
        "en": "{slot} dose completed",
        "id": "Obat {slot} selesai",
        "vi": "Đã uống thuốc {slot}",
        "th": "บันทึกยามื้อ{slot}แล้ว",
        "ja": "{slot}の服薬が完了しました",
    },
    "flex.med.alt.urgent": {
        "zh-TW": "CARE 提醒：您尚未確認 {slot} 服藥",
        "en": "CARE reminder: your {slot} dose is unconfirmed",
        "id": "Pengingat CARE: obat {slot} belum dikonfirmasi",
        "vi": "Nhắc nhở CARE: bạn chưa xác nhận uống thuốc {slot}",
        "th": "การแจ้งเตือน CARE: คุณยังไม่ยืนยันยามื้อ{slot}",
        "ja": "CARE リマインダー：{slot}の服薬が未確認です",
    },
    "flex.med.alt.caregiver": {
        "zh-TW": "關心提醒：{name} 逾時未服藥",
        "en": "Care alert: {name} has not taken their medication",
        "id": "Peringatan: {name} belum minum obat",
        "vi": "Cảnh báo: {name} chưa uống thuốc",
        "th": "แจ้งเตือน: {name} ยังไม่ได้ทานยา",
        "ja": "見守り通知：{name} さんが服薬していません",
    },
    "flex.med.header.reminder": {
        "zh-TW": "用藥提醒",
        "en": "Medication reminder",
        "id": "Pengingat obat",
        "vi": "Nhắc uống thuốc",
        "th": "เตือนทานยา",
        "ja": "服薬リマインダー",
    },
    "flex.med.header.done": {
        "zh-TW": "用藥已完成",
        "en": "Dose completed",
        "id": "Obat selesai",
        "vi": "Đã uống thuốc",
        "th": "ทานยาแล้ว",
        "ja": "服薬完了",
    },
    "flex.med.header.urgent": {
        "zh-TW": "尚未完成用藥",
        "en": "Dose not confirmed",
        "id": "Obat belum dikonfirmasi",
        "vi": "Chưa xác nhận uống thuốc",
        "th": "ยังไม่ได้ยืนยันการทานยา",
        "ja": "服薬が未確認です",
    },
    "flex.med.header.caregiver": {
        "zh-TW": "家人關心提醒",
        "en": "Family care alert",
        "id": "Peringatan keluarga",
        "vi": "Thông báo cho người thân",
        "th": "แจ้งเตือนถึงครอบครัว",
        "ja": "家族への見守り通知",
    },
    "flex.med.scheduled_at": {
        "zh-TW": "服藥時間 {time}",
        "en": "Scheduled for {time}",
        "id": "Dijadwalkan {time}",
        "vi": "Giờ uống {time}",
        "th": "เวลา {time}",
        "ja": "服薬時間 {time}",
    },
    "flex.med.instruction": {
        "zh-TW": "請於 30 分鐘內服藥，並點擊下方按鈕確認。",
        "en": "Please take your medication within 30 minutes and tap the button below to confirm.",
        "id": "Silakan minum obat dalam 30 menit dan ketuk tombol di bawah untuk konfirmasi.",
        "vi": "Vui lòng uống thuốc trong vòng 30 phút và nhấn nút bên dưới để xác nhận.",
        "th": "กรุณาทานยาภายใน 30 นาที แล้วแตะปุ่มด้านล่างเพื่อยืนยัน",
        "ja": "30 分以内に服薬し、下のボタンをタップして確認してください。",
    },
    "flex.med.button.taken": {
        "zh-TW": "我已用藥",
        "en": "I took it",
        "id": "Sudah diminum",
        "vi": "Tôi đã uống",
        "th": "ทานยาแล้ว",
        "ja": "服薬しました",
    },
    "flex.med.display.taken": {
        "zh-TW": "我已經完成用藥了",
        "en": "I have taken my medication",
        "id": "Saya sudah minum obat",
        "vi": "Tôi đã uống thuốc xong",
        "th": "ฉันทานยาเรียบร้อยแล้ว",
        "ja": "服薬を完了しました",
    },
    "flex.med.done_at": {
        "zh-TW": "已於 {time} 完成用藥",
        "en": "Taken at {time}",
        "id": "Diminum pada {time}",
        "vi": "Đã uống lúc {time}",
        "th": "ทานเมื่อ {time}",
        "ja": "{time} に服薬済み",
    },
    "flex.med.done": {
        "zh-TW": "已完成用藥",
        "en": "Dose completed",
        "id": "Obat sudah diminum",
        "vi": "Đã uống thuốc",
        "th": "ทานยาแล้ว",
        "ja": "服薬済み",
    },
    "flex.med.thanks": {
        "zh-TW": "感謝您的紀錄，服藥紀錄已成功登錄。",
        "en": "Thank you. Your medication record has been saved.",
        "id": "Terima kasih. Catatan obat Anda telah tersimpan.",
        "vi": "Cảm ơn bạn. Ghi nhận uống thuốc đã được lưu.",
        "th": "ขอบคุณค่ะ บันทึกการทานยาเรียบร้อยแล้ว",
        "ja": "ありがとうございます。服薬記録を保存しました。",
    },
    "flex.med.urgent_body": {
        "zh-TW": "您尚未點擊「我已用藥」。請即刻服藥並點擊下方按鈕確認。",
        "en": 'You have not tapped "I took it" yet. Please take your medication now and confirm below.',
        "id": 'Anda belum menekan "Sudah diminum". Silakan minum obat sekarang dan konfirmasi di bawah.',
        "vi": 'Bạn chưa nhấn "Tôi đã uống". Vui lòng uống thuốc ngay và xác nhận bên dưới.',
        "th": 'คุณยังไม่ได้แตะ "ทานยาแล้ว" กรุณาทานยาทันทีและยืนยันด้านล่าง',
        "ja": "「服薬しました」がまだタップされていません。すぐに服薬し、下のボタンで確認してください。",
    },
    "flex.med.overdue": {
        "zh-TW": "逾時 30 分鐘仍未完成用藥確認。",
        "en": "Still unconfirmed 30 minutes past the scheduled time.",
        "id": "Masih belum dikonfirmasi 30 menit setelah jadwal.",
        "vi": "Vẫn chưa xác nhận sau 30 phút kể từ giờ hẹn.",
        "th": "เลยเวลามา 30 นาทีแล้วแต่ยังไม่ยืนยัน",
        "ja": "予定時刻から 30 分経過しても確認がありません。",
    },
    "flex.med.please_care": {
        "zh-TW": "請您抽空致電或當面關心家人的狀況。",
        "en": "Please call or check in on your family member when you can.",
        "id": "Mohon hubungi atau tengok anggota keluarga Anda bila sempat.",
        "vi": "Vui lòng gọi điện hoặc đến thăm hỏi người thân khi có thể.",
        "th": "กรุณาโทรหรือแวะไปดูแลสมาชิกในครอบครัวเมื่อสะดวก",
        "ja": "お時間のあるときに、ご家族へ連絡または様子をご確認ください。",
    },
    # 系統中斷期間錯過的時段：措辭必須與 T+30 逾時警報區隔。
    # 那則是「家人沒有按時服藥」，這則是「我們沒能發出提醒，因此不知道有沒有服藥」。
    "flex.med.header.missed_summary": {
        "zh-TW": "系統中斷未能提醒",
        "en": "Reminders missed (service outage)",
        "id": "Pengingat terlewat (gangguan sistem)",
        "vi": "Bỏ lỡ nhắc nhở (sự cố hệ thống)",
        "th": "พลาดการแจ้งเตือน (ระบบขัดข้อง)",
        "ja": "システム停止により通知できませんでした",
    },
    "flex.med.alt.missed_summary": {
        "zh-TW": "{name} 有 {count} 個時段未發出服藥提醒",
        "en": "{count} reminder(s) not sent for {name}",
        "id": "{count} pengingat tidak terkirim untuk {name}",
        "vi": "{count} nhắc nhở chưa gửi cho {name}",
        "th": "มี {count} รายการที่ไม่ได้แจ้งเตือนสำหรับ {name}",
        "ja": "{name} の服薬通知 {count} 件が未送信です",
    },
    "flex.med.missed_summary_body": {
        "zh-TW": "系統中斷期間未能發出提醒，以下時段的服藥狀況無法確認。",
        "en": "Reminders could not be sent during a service outage, so the doses below are unconfirmed.",
        "id": "Pengingat tidak dapat dikirim saat sistem terganggu, sehingga dosis berikut belum dapat dipastikan.",
        "vi": "Không thể gửi nhắc nhở trong thời gian hệ thống gián đoạn, nên các cữ thuốc sau chưa được xác nhận.",
        "th": "ไม่สามารถส่งการแจ้งเตือนได้ในช่วงที่ระบบขัดข้อง จึงยังไม่สามารถยืนยันการรับประทานยาต่อไปนี้",
        "ja": "システム停止中は通知を送信できませんでした。以下の服薬状況は確認できていません。",
    },
    "flex.med.missed_summary_hint": {
        "zh-TW": "請確認家人是否已按時服藥。",
        "en": "Please check whether your family member has taken these doses.",
        "id": "Mohon periksa apakah anggota keluarga Anda sudah minum obat tersebut.",
        "vi": "Vui lòng kiểm tra xem người thân đã uống các cữ thuốc này chưa.",
        "th": "กรุณาตรวจสอบว่าสมาชิกในครอบครัวรับประทานยาเหล่านี้แล้วหรือยัง",
        "ja": "ご家族が服薬されたかどうかご確認ください。",
    },
    "flex.med.missed_summary_more": {
        "zh-TW": "…另有 {count} 個時段",
        "en": "…and {count} more",
        "id": "…dan {count} lainnya",
        "vi": "…và {count} cữ khác",
        "th": "…และอีก {count} รายการ",
        "ja": "…ほか {count} 件",
    },
    # --- 知識回報核准：URL 白名單錯誤（design.md Decision 7）---
    # 這三個 key 刻意只提供 zh-TW 與 en，且刻意不進
    # tests/unit/i18n/test_messages.py 的 REQUIRED_KEYS。那份清單是 LINE
    # 使用者面訊息的六語硬性要求；這三個字串只會出現在 admin 審核頁與
    # API 400 錯誤回應，受眾是營運人員，不是 LINE 使用者。t() 對缺語系
    # 會退回 zh-TW（見本檔案 t() 的實作），行為安全。
    # 這是刻意的取捨，不是漏做——如果你想「順手補齊六語」，請先看這裡。
    "url.reject.summary": {
        "zh-TW": "以下 {count} 個網址未通過來源白名單，請檢查後重新送出。",
        "en": (
            "The following {count} URL(s) did not pass the source whitelist. "
            "Please review and resubmit."
        ),
    },
    # 對應 InvalidUrl.reason 的可讀標籤，供呈現層需要時把 invalid_urls[].reason
    # 轉成人看得懂的文字。reason 本身在 API 回應中維持機器可讀代碼
    # （"malformed"／"not_allowed"），不因為有翻譯就改變契約。
    "url.reject.reason.malformed": {
        "zh-TW": "網址格式錯誤",
        "en": "Malformed URL",
    },
    "url.reject.reason.not_allowed": {
        "zh-TW": "網域不在來源白名單內",
        "en": "Domain not in the source whitelist",
    },
    # --- 核准前的內容預覽 ---
    # 與上面三個 key 相同的取捨：受眾是 admin 審核頁與 API 錯誤回應，不是 LINE
    # 使用者，所以只提供 zh-TW 與 en，也不進 REQUIRED_KEYS 的六語硬性要求。
    "preview.reject.too_many_urls": {
        "zh-TW": "一次最多只能預覽 {max} 個網址，請減少選取數量後重試。",
        "en": "At most {max} URL(s) can be previewed at once. Please select fewer.",
    },
    "preview.stale.missing": {
        "zh-TW": "尚未取得內容預覽，請先抓取內容再核准。",
        "en": "No content preview yet. Fetch the content before approving.",
    },
    "preview.stale.expired": {
        "zh-TW": "內容預覽已逾期，請重新抓取後再核准。",
        "en": "The content preview has expired. Please fetch it again before approving.",
    },
    "preview.stale.superseded": {
        "zh-TW": "內容預覽已被更新的一份取代，請重新檢視後再核准。",
        "en": "The content preview has been superseded. Please review the new one.",
    },
    "preview.stale.url_missing": {
        "zh-TW": "以下網址沒有成功的預覽內容，無法核准：{urls}",
        "en": "The following URL(s) have no successful preview content: {urls}",
    },
    "preview.stale.hash_mismatch": {
        "zh-TW": "以下網址的內容與你檢視的版本不符，請重新檢視後再核准：{urls}",
        "en": (
            "The content of the following URL(s) differs from what you reviewed: "
            "{urls}"
        ),
    },
    # --- Flex：服藥提醒／二次催促的藥品清單區塊 ---
    # 適應症等其他欄位一律不進入這裡，見 medication_flex.py。
    "flex.med.medication_list_heading": {
        "zh-TW": "本次應服藥品",
        "en": "Medications for this dose",
        "id": "Obat untuk dosis ini",
        "vi": "Thuốc cho lần uống này",
        "th": "ยาสำหรับมื้อนี้",
        "ja": "今回服用する薬",
    },
    # 用藥已完成卡片的標題。與上一個 key 分開，是因為時態不同：確認之後再說
    # 「應服」會讓使用者以為還有東西沒吃，而這張卡片的用途正是「留下吃了什麼
    # 的紀錄」——事後回頭翻訊息時，這行字要能直接回答「那次我吃了哪幾種藥」。
    "flex.med.medication_list_heading_done": {
        "zh-TW": "本次服用藥品",
        "en": "Medications taken",
        "id": "Obat yang diminum",
        "vi": "Thuốc đã uống",
        "th": "ยาที่ทานแล้ว",
        "ja": "服用した薬",
    },
    # 家屬逾時警報的標題。收件人是規則的建立者（alert_notify_user_id ==
    # creator_user_id），也就是當初替家人設定這些藥的人；讓警報講清楚是哪幾種
    # 藥沒吃，家屬才知道這次漏掉的嚴重程度，不必再回頭翻 LIFF 才能判斷。
    "flex.med.medication_list_heading_missed": {
        "zh-TW": "尚未服用的藥品",
        "en": "Medications not yet taken",
        "id": "Obat yang belum diminum",
        "vi": "Thuốc chưa uống",
        "th": "ยาที่ยังไม่ได้ทาน",
        "ja": "まだ服用していない薬",
    },
    # 與 flex.med.missed_summary_more 同一種收斂形狀：超過顯示上限的品項不逐一列出，
    # 收斂成一行計數，避免藥品數量過多時訊息過長。
    "flex.med.medication_list_more": {
        "zh-TW": "…另有 {count} 種藥品",
        "en": "…and {count} more medication(s)",
        "id": "…dan {count} obat lainnya",
        "vi": "…và {count} loại thuốc khác",
        "th": "…และอีก {count} รายการยา",
        "ja": "…ほか {count} 件の薬",
    },
    # 依飯前／飯後分區的小標（design 決策 6）。全型空格與家屬彙整通知的
    # 「{slot_name}　{scheduled_time}」同一種排版，六語共用同一個模板字串，
    # 差別只在前面代換進去的 meal 名稱是否已翻譯。
    "flex.med.group_heading": {
        "zh-TW": "{meal}　{time}",
        "en": "{meal}　{time}",
        "id": "{meal}　{time}",
        "vi": "{meal}　{time}",
        "th": "{meal}　{time}",
        "ja": "{meal}　{time}",
    },
    # 逐藥確認：每一列右側的小按鈕，文案要比整批的「我已用藥」更短——
    # 一列的可用寬度扣掉藥名與按鈕本身的內距後所剩無幾，長文案會被截斷。
    "flex.med.button.taken_one": {
        "zh-TW": "已吃", "en": "Taken", "id": "Sudah",
        "vi": "Đã uống", "th": "ทานแล้ว", "ja": "服用済み",
    },
    "flex.med.display.taken_one": {
        "zh-TW": "我吃了 {name}",
        "en": "I took {name}",
        "id": "Saya sudah minum {name}",
        "vi": "Tôi đã uống {name}",
        "th": "ฉันทาน {name} แล้ว",
        "ja": "{name} を服用しました",
    },
    # 逐藥確認上線後，底部整批按鈕改用這個文案（原本的 flex.med.button.taken
    # 仍是「已完成」卡片與逾時催促既有按鈕共用的文案，這裡另開一個 key 而不是
    # 直接改字，是因為「我已用藥」在沒有 medication_groups 的既有版面裡還是
    # 對的措辭，兩者語意不同不該共用同一個 key）。
    "flex.med.button.taken_all": {
        "zh-TW": "全部已服用",
        "en": "All taken",
        "id": "Semua sudah diminum",
        "vi": "Đã uống hết",
        "th": "ทานครบแล้ว",
        "ja": "すべて服用済み",
    },
    # 用藥風險偵測。給當事人的兩則一律純文字（見 line-reply-rules），措辭刻意
    # 不帶指責：這個功能最容易的失敗方式是讓長輩覺得被監視而不再發問。
    "safety.patient.low": {
        "zh-TW": "「{drug}」這個名字，我在台灣核准的藥品資料裡查不到。可能只是簡稱或寫法不同，方便的話可以拍一下包裝上的完整名稱，我再幫您看看。",
        "en": "I couldn't find \"{drug}\" among medicines approved in Taiwan. It may simply be a short name or a different spelling. If it's convenient, send me a photo of the full name on the package and I'll take another look.",
        "id": "Saya tidak menemukan \"{drug}\" dalam daftar obat yang disetujui di Taiwan. Mungkin itu hanya nama singkat atau ejaan yang berbeda. Jika berkenan, kirimkan foto nama lengkap pada kemasannya dan saya akan memeriksanya lagi.",
        "vi": "Tôi không tìm thấy \"{drug}\" trong danh mục thuốc được cấp phép tại Đài Loan. Có thể đó chỉ là tên gọi tắt hoặc cách viết khác. Nếu tiện, bạn hãy chụp tên đầy đủ trên bao bì để tôi xem lại giúp bạn.",
        "th": "ฉันไม่พบ \"{drug}\" ในรายการยาที่ได้รับอนุญาตในไต้หวัน อาจเป็นเพียงชื่อย่อหรือสะกดต่างกัน หากสะดวก กรุณาถ่ายรูปชื่อเต็มบนบรรจุภัณฑ์ แล้วฉันจะช่วยดูอีกครั้ง",
        "ja": "「{drug}」という名称は、台湾で承認された医薬品のデータでは見つかりませんでした。略称や表記の違いだけかもしれません。よろしければ、パッケージに書かれた正式名称を撮って送ってください。もう一度確認します。",
    },
    "safety.patient.high": {
        "zh-TW": "關於「{drug}」，{reason}。這類藥品在台灣沒有經過查驗登記，成分與劑量無從確認，先不要繼續服用，找醫師或藥師看一下比較妥當。我已經請家人一起看看。",
        "en": "About \"{drug}\": {reason}. Medicines like this haven't gone through registration review in Taiwan, so their ingredients and dosage can't be verified. Please hold off on taking it and check with a doctor or pharmacist. I've also asked your family to take a look.",
        "id": "Tentang \"{drug}\": {reason}. Obat seperti ini belum melalui pendaftaran resmi di Taiwan, sehingga kandungan dan dosisnya tidak dapat dipastikan. Sebaiknya hentikan dulu dan periksakan ke dokter atau apoteker. Saya juga sudah meminta keluarga Anda untuk ikut melihat.",
        "vi": "Về \"{drug}\": {reason}. Những thuốc như thế này chưa qua đăng ký thẩm định tại Đài Loan nên không thể xác minh thành phần và liều lượng. Bạn hãy tạm ngưng dùng và hỏi bác sĩ hoặc dược sĩ. Tôi cũng đã nhờ người thân của bạn cùng xem giúp.",
        "th": "เกี่ยวกับ \"{drug}\": {reason} ยาลักษณะนี้ยังไม่ผ่านการขึ้นทะเบียนในไต้หวัน จึงไม่สามารถยืนยันส่วนประกอบและขนาดยาได้ กรุณาหยุดใช้ไว้ก่อนและปรึกษาแพทย์หรือเภสัชกร ฉันได้แจ้งให้ครอบครัวของคุณช่วยดูด้วยแล้ว",
        "ja": "「{drug}」についてですが、{reason}。この種の医薬品は台湾で承認審査を受けていないため、成分や用量を確認できません。服用はいったん止めて、医師か薬剤師に相談してください。ご家族にも一緒に確認していただくようお伝えしました。",
    },
    # 與上一則的差別只有最後一句：**沒有任何合格收件人時不得聲稱家人已被告知**。
    #
    # 告訴一位長輩「我已經請家人一起看看」而實際上沒有任何人收到，比不通知更糟
    # ——他會以為有人正在處理，於是不再自己找醫師。收件人可為空之後，那句話就
    # 不能無條件講。
    "safety.patient.high_no_family": {
        "zh-TW": "關於「{drug}」，{reason}。這類藥品在台灣沒有經過查驗登記，成分與劑量無從確認，先不要繼續服用，找醫師或藥師看一下比較妥當。",
        "en": "About \"{drug}\": {reason}. Medicines like this haven't gone through registration review in Taiwan, so their ingredients and dosage can't be verified. Please hold off on taking it and check with a doctor or pharmacist.",
        "id": "Tentang \"{drug}\": {reason}. Obat seperti ini belum melalui pendaftaran resmi di Taiwan, sehingga kandungan dan dosisnya tidak dapat dipastikan. Sebaiknya hentikan dulu dan periksakan ke dokter atau apoteker.",
        "vi": "Về \"{drug}\": {reason}. Những thuốc như thế này chưa qua đăng ký thẩm định tại Đài Loan nên không thể xác minh thành phần và liều lượng. Bạn hãy tạm ngưng dùng và hỏi bác sĩ hoặc dược sĩ.",
        "th": "เกี่ยวกับ \"{drug}\": {reason} ยาลักษณะนี้ยังไม่ผ่านการขึ้นทะเบียนในไต้หวัน จึงไม่สามารถยืนยันส่วนประกอบและขนาดยาได้ กรุณาหยุดใช้ไว้ก่อนและปรึกษาแพทย์หรือเภสัชกร",
        "ja": "「{drug}」についてですが、{reason}。この種の医薬品は台湾で承認審査を受けていないため、成分や用量を確認できません。服用はいったん止めて、医師か薬剤師に相談してください。",
    },
    # 風險類型的說明。刻意只描述訊號本身（外文標示、不明通路），不描述病情，
    # 也不重述使用者的原話——通報訊息會出現在通知列與鎖定畫面。
    "safety.reason.foreign_version": {
        "zh-TW": "包裝上有外文標示，看起來不是台灣核准的版本",
        "en": "the packaging carries foreign-language labelling and doesn't look like the version approved in Taiwan",
        "id": "kemasannya memuat label berbahasa asing dan tampaknya bukan versi yang disetujui di Taiwan",
        "vi": "bao bì có nhãn tiếng nước ngoài và có vẻ không phải phiên bản được cấp phép tại Đài Loan",
        "th": "บรรจุภัณฑ์มีฉลากภาษาต่างประเทศ และดูเหมือนไม่ใช่รุ่นที่ได้รับอนุญาตในไต้หวัน",
        "ja": "パッケージに外国語の表示があり、台湾で承認された版ではないようです",
    },
    "safety.reason.unverified_channel": {
        "zh-TW": "取得的管道不是醫療機構或合法藥局",
        "en": "it wasn't obtained from a medical institution or a licensed pharmacy",
        "id": "obat ini tidak diperoleh dari fasilitas medis atau apotek berizin",
        "vi": "thuốc không được lấy từ cơ sở y tế hoặc nhà thuốc được cấp phép",
        "th": "ไม่ได้รับมาจากสถานพยาบาลหรือร้านขายยาที่ได้รับอนุญาต",
        "ja": "入手経路が医療機関でも正規の薬局でもありません",
    },
    "flex.safety.header.family": {
        "zh-TW": "用藥安全提醒",
        "en": "Medication safety alert",
        "id": "Peringatan keamanan obat",
        "vi": "Cảnh báo an toàn thuốc",
        "th": "แจ้งเตือนความปลอดภัยด้านยา",
        "ja": "医薬品安全のお知らせ",
    },
    "flex.safety.family.intro": {
        "zh-TW": "提到了這個藥品",
        "en": "mentioned this medication",
        "id": "menyebutkan obat ini",
        "vi": "đã nhắc đến loại thuốc này",
        "th": "ได้กล่าวถึงยานี้",
        "ja": "この薬について話していました",
    },
    "flex.safety.family.please_check": {
        "zh-TW": "請找個時間一起確認來源，必要時陪同就醫。當事人也收到了同一則提醒。",
        "en": "Please find a moment to check where it came from together, and see a doctor if needed. They have received the same notice.",
        "id": "Mohon luangkan waktu untuk memeriksa asal obat ini bersama, dan periksakan ke dokter bila perlu. Yang bersangkutan juga menerima pemberitahuan yang sama.",
        "vi": "Xin hãy dành thời gian cùng kiểm tra nguồn gốc thuốc, và đi khám nếu cần. Người đó cũng đã nhận được thông báo tương tự.",
        "th": "กรุณาหาเวลาตรวจสอบแหล่งที่มาร่วมกัน และพาไปพบแพทย์หากจำเป็น เจ้าตัวได้รับการแจ้งเตือนเดียวกันแล้ว",
        "ja": "お時間のあるときに入手先を一緒にご確認いただき、必要なら受診に付き添ってください。ご本人にも同じ通知が届いています。",
    },
    "flex.safety.alt.family": {
        "zh-TW": "{name} 的用藥安全提醒",
        "en": "Medication safety alert for {name}",
        "id": "Peringatan keamanan obat untuk {name}",
        "vi": "Cảnh báo an toàn thuốc cho {name}",
        "th": "แจ้งเตือนความปลอดภัยด้านยาของ {name}",
        "ja": "{name} さんの医薬品安全のお知らせ",
    },
    # --- 非處方藥成分重複提醒 -------------------------------------------
    # altText 刻意不帶藥名與用途：它就是通知列與鎖定畫面上顯示的那一行，
    # 可能被非預期的人看到。藥名與用途留在卡片內容裡——收件人已由通知政策
    # 收斂為 GUARDIAN／CAREGIVER，他們依授權矩陣本來就看得到 SENSITIVE。
    "flex.otc.header.overlap": {
        "zh-TW": "用藥重複提醒",
        "en": "Duplicate ingredient notice",
        "id": "Pemberitahuan bahan obat ganda",
        "vi": "Thông báo trùng hoạt chất",
        "th": "แจ้งเตือนตัวยาซ้ำ",
        "ja": "成分重複のお知らせ",
    },
    "flex.otc.header.added": {
        "zh-TW": "新增了用藥提醒",
        "en": "New medication reminder",
        "id": "Pengingat obat baru",
        "vi": "Nhắc thuốc mới",
        "th": "เพิ่มการเตือนกินยาใหม่",
        "ja": "服薬リマインダーを追加しました",
    },
    "flex.otc.alt.overlap": {
        "zh-TW": "{name} 的用藥重複提醒",
        "en": "Duplicate ingredient notice for {name}",
        "id": "Pemberitahuan bahan obat ganda untuk {name}",
        "vi": "Thông báo trùng hoạt chất của {name}",
        "th": "แจ้งเตือนตัวยาซ้ำของ {name}",
        "ja": "{name} さんの成分重複のお知らせ",
    },
    "flex.otc.alt.added": {
        "zh-TW": "{name} 新增了用藥提醒",
        "en": "{name} added a medication reminder",
        "id": "{name} menambahkan pengingat obat",
        "vi": "{name} đã thêm một nhắc thuốc",
        "th": "{name} เพิ่มการเตือนกินยา",
        "ja": "{name} さんが服薬リマインダーを追加しました",
    },
    "flex.otc.intro.overlap": {
        "zh-TW": "剛加入的這個藥，和已經在吃的藥含有相同成分",
        "en": "just added this medication, which shares an ingredient with one already being taken",
        "id": "baru menambahkan obat ini, yang memiliki bahan sama dengan obat yang sedang diminum",
        "vi": "vừa thêm thuốc này, trùng hoạt chất với thuốc đang dùng",
        "th": "เพิ่งเพิ่มยานี้ ซึ่งมีตัวยาซ้ำกับยาที่กินอยู่",
        "ja": "この薬を追加しました。すでに飲んでいる薬と同じ成分が含まれています",
    },
    "flex.otc.intro.added": {
        "zh-TW": "剛加入了這個不用處方就能買到的藥",
        "en": "just added this over-the-counter medication",
        "id": "baru menambahkan obat bebas ini",
        "vi": "vừa thêm loại thuốc không kê đơn này",
        "th": "เพิ่งเพิ่มยาที่ซื้อได้เองชนิดนี้",
        "ja": "処方箋なしで買えるこの薬を追加しました",
    },
    "flex.otc.label.existing": {
        "zh-TW": "已經在吃",
        "en": "Already taking",
        "id": "Sudah diminum",
        "vi": "Đang dùng",
        "th": "กินอยู่แล้ว",
        "ja": "すでに服用中",
    },
    "flex.otc.label.shared": {
        "zh-TW": "相同成分",
        "en": "Shared ingredient",
        "id": "Bahan yang sama",
        "vi": "Hoạt chất trùng",
        "th": "ตัวยาที่ซ้ำ",
        "ja": "重複している成分",
    },
    "flex.otc.label.indication": {
        "zh-TW": "用途",
        "en": "Used for",
        "id": "Kegunaan",
        "vi": "Công dụng",
        "th": "ใช้สำหรับ",
        "ja": "効能",
    },
    "flex.otc.please_check.overlap": {
        "zh-TW": "同一種成分吃到兩份可能會過量。請找個時間一起看一下，或把兩盒藥拿給藥師確認。當事人也收到了同一則提醒。",
        "en": "Taking the same ingredient twice can add up to too much. Please take a moment to look at both together, or show the two boxes to a pharmacist. They have received the same notice.",
        "id": "Bahan yang sama diminum dua kali bisa menjadi berlebihan. Mohon lihat keduanya bersama, atau tunjukkan kedua kotak obat ke apoteker. Yang bersangkutan juga menerima pemberitahuan yang sama.",
        "vi": "Dùng cùng một hoạt chất hai lần có thể thành quá liều. Xin hãy cùng xem qua cả hai, hoặc mang hai hộp thuốc đến hỏi dược sĩ. Người đó cũng đã nhận được thông báo tương tự.",
        "th": "การได้รับตัวยาเดียวกันซ้ำอาจเกินขนาด กรุณาหาเวลาดูด้วยกัน หรือนำยาทั้งสองกล่องไปให้เภสัชกรตรวจสอบ เจ้าตัวได้รับการแจ้งเตือนเดียวกันแล้ว",
        "ja": "同じ成分を二重に飲むと量が多くなりすぎることがあります。お時間のあるときに一緒にご確認いただくか、両方の箱を薬剤師にお見せください。ご本人にも同じ通知が届いています。",
    },
    "flex.otc.please_check.added": {
        "zh-TW": "這類藥不用處方就能買到，很容易和家裡原有的藥重複。若他還在吃別的藥，請留意一下。",
        "en": "Medications like this can be bought without a prescription, so they easily overlap with what is already at home. If they are taking anything else, please keep an eye on it.",
        "id": "Obat seperti ini bisa dibeli tanpa resep, sehingga mudah tumpang tindih dengan obat yang sudah ada di rumah. Bila beliau minum obat lain, mohon diperhatikan.",
        "vi": "Loại thuốc này mua được không cần đơn nên rất dễ trùng với thuốc sẵn có ở nhà. Nếu người đó còn dùng thuốc khác, xin để ý giúp.",
        "th": "ยาแบบนี้ซื้อได้เองโดยไม่ต้องมีใบสั่งยา จึงซ้ำกับยาที่มีอยู่ที่บ้านได้ง่าย หากท่านกินยาอื่นอยู่ กรุณาช่วยสังเกตด้วย",
        "ja": "この種の薬は処方箋なしで買えるため、家にある薬と重なりやすいです。ほかにも飲んでいる薬があれば、気にかけてあげてください。",
    },
    # 給當事人的訊息。SHALL NOT 給劑量建議、SHALL NOT 指示停藥——那是藥事人員
    # 的判斷。措辭是「讓家人幫你看一下」而不是「已通報家人」：後者讓長輩覺得
    # 掃描等於被監控，下次就不掃了，那會連帶失去這個功能想保護的一切。
    "text.otc.patient.overlap": {
        "zh-TW": "你剛加入的「{new_drug}」，和已經在吃的「{existing_drug}」含有相同成分（{ingredients}）。\n\n這兩種藥不一定不能一起吃，但最好請藥師看一下。下次經過藥局時，把兩盒藥一起帶去問他就可以了。\n\n也讓家人幫你看一下，比較放心。",
        "en": "The 「{new_drug}」 you just added shares an ingredient ({ingredients}) with 「{existing_drug}」, which you are already taking.\n\nThat does not necessarily mean they cannot be taken together, but it is best to ask a pharmacist. Next time you pass a pharmacy, bring both boxes and ask.\n\nYour family can take a look with you too.",
        "id": "「{new_drug}」 yang baru Anda tambahkan memiliki bahan yang sama ({ingredients}) dengan 「{existing_drug}」 yang sedang Anda minum.\n\nItu belum tentu berarti keduanya tidak boleh diminum bersama, tetapi sebaiknya tanyakan ke apoteker. Lain kali saat melewati apotek, bawalah kedua kotak obat dan tanyakan.\n\nKeluarga juga bisa ikut melihatnya bersama Anda.",
        "vi": "「{new_drug}」 bạn vừa thêm có cùng hoạt chất ({ingredients}) với 「{existing_drug}」 bạn đang dùng.\n\nĐiều đó không hẳn là không thể dùng chung, nhưng nên hỏi dược sĩ. Lần tới đi ngang nhà thuốc, hãy mang cả hai hộp để hỏi.\n\nNgười nhà cũng có thể xem cùng bạn.",
        "th": "「{new_drug}」 ที่คุณเพิ่งเพิ่ม มีตัวยาซ้ำ ({ingredients}) กับ 「{existing_drug}」 ที่คุณกินอยู่\n\nไม่ได้แปลว่ากินด้วยกันไม่ได้ แต่ควรถามเภสัชกร ครั้งหน้าที่ผ่านร้านยา นำยาทั้งสองกล่องไปถามได้เลย\n\nให้คนที่บ้านช่วยดูด้วยจะอุ่นใจกว่า",
        "ja": "追加された「{new_drug}」は、すでに飲んでいる「{existing_drug}」と同じ成分（{ingredients}）を含んでいます。\n\n必ずしも一緒に飲めないわけではありませんが、薬剤師に見てもらうのが安心です。今度薬局に立ち寄ったとき、両方の箱を持って聞いてみてください。\n\nご家族にも一緒に見てもらいましょう。",
    },
    "text.otc.patient.overlap_solo": {
        "zh-TW": "你剛加入的「{new_drug}」，和已經在吃的「{existing_drug}」含有相同成分（{ingredients}）。\n\n這兩種藥不一定不能一起吃，但最好請藥師看一下。下次經過藥局時，把兩盒藥一起帶去問他就可以了。",
        "en": "The 「{new_drug}」 you just added shares an ingredient ({ingredients}) with 「{existing_drug}」, which you are already taking.\n\nThat does not necessarily mean they cannot be taken together, but it is best to ask a pharmacist. Next time you pass a pharmacy, bring both boxes and ask.",
        "id": "「{new_drug}」 yang baru Anda tambahkan memiliki bahan yang sama ({ingredients}) dengan 「{existing_drug}」 yang sedang Anda minum.\n\nItu belum tentu berarti keduanya tidak boleh diminum bersama, tetapi sebaiknya tanyakan ke apoteker. Lain kali saat melewati apotek, bawalah kedua kotak obat dan tanyakan.",
        "vi": "「{new_drug}」 bạn vừa thêm có cùng hoạt chất ({ingredients}) với 「{existing_drug}」 bạn đang dùng.\n\nĐiều đó không hẳn là không thể dùng chung, nhưng nên hỏi dược sĩ. Lần tới đi ngang nhà thuốc, hãy mang cả hai hộp để hỏi.",
        "th": "「{new_drug}」 ที่คุณเพิ่งเพิ่ม มีตัวยาซ้ำ ({ingredients}) กับ 「{existing_drug}」 ที่คุณกินอยู่\n\nไม่ได้แปลว่ากินด้วยกันไม่ได้ แต่ควรถามเภสัชกร ครั้งหน้าที่ผ่านร้านยา นำยาทั้งสองกล่องไปถามได้เลย",
        "ja": "追加された「{new_drug}」は、すでに飲んでいる「{existing_drug}」と同じ成分（{ingredients}）を含んでいます。\n\n必ずしも一緒に飲めないわけではありませんが、薬剤師に見てもらうのが安心です。今度薬局に立ち寄ったとき、両方の箱を持って聞いてみてください。",
    },
    # ── 抗膽鹼疊加（同類累加）────────────────────────────────────────────
    #
    # 與成分重複是兩種不同的事，措辭必須分開：重複是「同一個成分吃了兩份」，
    # 疊加是「兩個不同成分的作用加在一起」。對長輩講機轉沒有用，講他自己
    # 感覺得到的後果才有用——Beers Table 5 對這組列的風險就是意識混亂、
    # 跌倒與骨折，源頭是想睡、頭暈、口乾這些可自我察覺的症狀。
    #
    # 與 overlap 同一條紅線：SHALL NOT 給劑量建議、SHALL NOT 指示停藥。
    "flex.otc.header.stacking": {
        "zh-TW": "用藥作用疊加提醒",
        "en": "Overlapping drug effects",
        "id": "Efek obat yang menumpuk",
        "vi": "Tác dụng thuốc chồng nhau",
        "th": "แจ้งเตือนฤทธิ์ยาซ้อนทับ",
        "ja": "作用が重なる薬のお知らせ",
    },
    "flex.otc.alt.stacking": {
        "zh-TW": "{name} 的用藥作用疊加提醒",
        "en": "Overlapping drug effects for {name}",
        "id": "Efek obat yang menumpuk untuk {name}",
        "vi": "Tác dụng thuốc chồng nhau của {name}",
        "th": "แจ้งเตือนฤทธิ์ยาซ้อนทับของ {name}",
        "ja": "{name} さんの作用が重なる薬のお知らせ",
    },
    "flex.otc.intro.stacking": {
        "zh-TW": "剛加入的這個藥，和已經在吃的藥作用會疊在一起",
        "en": "just added this medication, whose effect adds to one already being taken",
        "id": "baru menambahkan obat ini, yang efeknya menumpuk dengan obat yang sedang diminum",
        "vi": "vừa thêm thuốc này, tác dụng sẽ cộng thêm với thuốc đang dùng",
        "th": "เพิ่งเพิ่มยานี้ ซึ่งฤทธิ์จะซ้อนทับกับยาที่กินอยู่",
        "ja": "この薬を追加しました。すでに飲んでいる薬と作用が重なります",
    },
    "flex.otc.label.stacking": {
        "zh-TW": "作用相似的成分",
        "en": "Ingredients with similar effects",
        "id": "Bahan dengan efek serupa",
        "vi": "Hoạt chất có tác dụng tương tự",
        "th": "ตัวยาที่ออกฤทธิ์คล้ายกัน",
        "ja": "作用が似ている成分",
    },
    "flex.otc.please_check.stacking": {
        "zh-TW": "這兩個成分不一樣，但作用會加在一起，容易讓人想睡、頭暈、口乾。對長輩來說，最需要留意的是跌倒。請找個時間一起看一下，或把兩盒藥拿給藥師確認。當事人也收到了同一則提醒。",
        "en": "These two ingredients are different, but their effects add up and can cause drowsiness, dizziness and dry mouth. For an older adult, the main concern is a fall. Please take a moment to look at both together, or show the two boxes to a pharmacist. They have received the same notice.",
        "id": "Kedua bahan ini berbeda, tetapi efeknya menumpuk dan bisa menyebabkan mengantuk, pusing, dan mulut kering. Bagi lansia, yang paling perlu diwaspadai adalah jatuh. Mohon lihat keduanya bersama, atau tunjukkan kedua kotak obat ke apoteker. Yang bersangkutan juga menerima pemberitahuan yang sama.",
        "vi": "Hai hoạt chất này khác nhau, nhưng tác dụng cộng lại có thể gây buồn ngủ, chóng mặt và khô miệng. Với người cao tuổi, điều đáng lo nhất là té ngã. Xin hãy cùng xem qua cả hai, hoặc mang hai hộp thuốc đến hỏi dược sĩ. Người đó cũng đã nhận được thông báo tương tự.",
        "th": "ตัวยาสองชนิดนี้ต่างกัน แต่ฤทธิ์จะเสริมกัน ทำให้ง่วง เวียนศีรษะ และปากแห้ง สำหรับผู้สูงอายุ สิ่งที่ต้องระวังที่สุดคือการหกล้ม กรุณาหาเวลาดูด้วยกัน หรือนำยาทั้งสองกล่องไปให้เภสัชกรตรวจสอบ เจ้าตัวได้รับการแจ้งเตือนเดียวกันแล้ว",
        "ja": "この二つの成分は別のものですが、作用が重なって眠気・めまい・口の渇きが出やすくなります。ご高齢の方で最も心配なのは転倒です。お時間のあるときに一緒にご確認いただくか、両方の箱を薬剤師にお見せください。ご本人にも同じ通知が届いています。",
    },
    "text.otc.patient.stacking": {
        "zh-TW": "你剛加入的「{new_drug}」，和已經在吃的「{existing_drug}」，成分不一樣，但作用會加在一起。\n\n這樣比較容易想睡、頭暈、口乾，走路要特別小心。\n\n這兩種藥不一定不能一起吃，但最好請藥師看一下。下次經過藥局時，把兩盒藥一起帶去問他就可以了。\n\n也讓家人幫你看一下，比較放心。",
        "en": "The 「{new_drug}」 you just added and 「{existing_drug}」, which you are already taking, contain different ingredients, but their effects add up.\n\nThat can make you drowsy, dizzy or dry in the mouth, so please take extra care when walking.\n\nThat does not necessarily mean they cannot be taken together, but it is best to ask a pharmacist. Next time you pass a pharmacy, bring both boxes and ask.\n\nYour family can take a look with you too.",
        "id": "「{new_drug}」 yang baru Anda tambahkan dan 「{existing_drug}」 yang sedang Anda minum memiliki bahan berbeda, tetapi efeknya menumpuk.\n\nIni bisa membuat mengantuk, pusing, atau mulut kering, jadi berhati-hatilah saat berjalan.\n\nItu belum tentu berarti keduanya tidak boleh diminum bersama, tetapi sebaiknya tanyakan ke apoteker. Lain kali saat melewati apotek, bawalah kedua kotak obat dan tanyakan.\n\nKeluarga juga bisa ikut melihatnya bersama Anda.",
        "vi": "「{new_drug}」 bạn vừa thêm và 「{existing_drug}」 bạn đang dùng có hoạt chất khác nhau, nhưng tác dụng sẽ cộng lại.\n\nĐiều đó dễ gây buồn ngủ, chóng mặt, khô miệng, nên hãy cẩn thận khi đi lại.\n\nĐiều đó không hẳn là không thể dùng chung, nhưng nên hỏi dược sĩ. Lần tới đi ngang nhà thuốc, hãy mang cả hai hộp để hỏi.\n\nNgười nhà cũng có thể xem cùng bạn.",
        "th": "「{new_drug}」 ที่คุณเพิ่งเพิ่ม กับ 「{existing_drug}」 ที่คุณกินอยู่ มีตัวยาต่างกัน แต่ฤทธิ์จะเสริมกัน\n\nอาจทำให้ง่วง เวียนศีรษะ ปากแห้ง เดินเหินต้องระวังเป็นพิเศษ\n\nไม่ได้แปลว่ากินด้วยกันไม่ได้ แต่ควรถามเภสัชกร ครั้งหน้าที่ผ่านร้านยา นำยาทั้งสองกล่องไปถามได้เลย\n\nให้คนที่บ้านช่วยดูด้วยจะอุ่นใจกว่า",
        "ja": "追加された「{new_drug}」と、すでに飲んでいる「{existing_drug}」は、成分は違いますが作用が重なります。\n\n眠気・めまい・口の渇きが出やすくなるので、歩くときは特に気をつけてください。\n\n必ずしも一緒に飲めないわけではありませんが、薬剤師に見てもらうのが安心です。今度薬局に立ち寄ったとき、両方の箱を持って聞いてみてください。\n\nご家族にも一緒に見てもらいましょう。",
    },
    "text.otc.patient.stacking_solo": {
        "zh-TW": "你剛加入的「{new_drug}」，和已經在吃的「{existing_drug}」，成分不一樣，但作用會加在一起。\n\n這樣比較容易想睡、頭暈、口乾，走路要特別小心。\n\n這兩種藥不一定不能一起吃，但最好請藥師看一下。下次經過藥局時，把兩盒藥一起帶去問他就可以了。",
        "en": "The 「{new_drug}」 you just added and 「{existing_drug}」, which you are already taking, contain different ingredients, but their effects add up.\n\nThat can make you drowsy, dizzy or dry in the mouth, so please take extra care when walking.\n\nThat does not necessarily mean they cannot be taken together, but it is best to ask a pharmacist. Next time you pass a pharmacy, bring both boxes and ask.",
        "id": "「{new_drug}」 yang baru Anda tambahkan dan 「{existing_drug}」 yang sedang Anda minum memiliki bahan berbeda, tetapi efeknya menumpuk.\n\nIni bisa membuat mengantuk, pusing, atau mulut kering, jadi berhati-hatilah saat berjalan.\n\nItu belum tentu berarti keduanya tidak boleh diminum bersama, tetapi sebaiknya tanyakan ke apoteker. Lain kali saat melewati apotek, bawalah kedua kotak obat dan tanyakan.",
        "vi": "「{new_drug}」 bạn vừa thêm và 「{existing_drug}」 bạn đang dùng có hoạt chất khác nhau, nhưng tác dụng sẽ cộng lại.\n\nĐiều đó dễ gây buồn ngủ, chóng mặt, khô miệng, nên hãy cẩn thận khi đi lại.\n\nĐiều đó không hẳn là không thể dùng chung, nhưng nên hỏi dược sĩ. Lần tới đi ngang nhà thuốc, hãy mang cả hai hộp để hỏi.",
        "th": "「{new_drug}」 ที่คุณเพิ่งเพิ่ม กับ 「{existing_drug}」 ที่คุณกินอยู่ มีตัวยาต่างกัน แต่ฤทธิ์จะเสริมกัน\n\nอาจทำให้ง่วง เวียนศีรษะ ปากแห้ง เดินเหินต้องระวังเป็นพิเศษ\n\nไม่ได้แปลว่ากินด้วยกันไม่ได้ แต่ควรถามเภสัชกร ครั้งหน้าที่ผ่านร้านยา นำยาทั้งสองกล่องไปถามได้เลย",
        "ja": "追加された「{new_drug}」と、すでに飲んでいる「{existing_drug}」は、成分は違いますが作用が重なります。\n\n眠気・めまい・口の渇きが出やすくなるので、歩くときは特に気をつけてください。\n\n必ずしも一緒に飲めないわけではありませんが、薬剤師に見てもらうのが安心です。今度薬局に立ち寄ったとき、両方の箱を持って聞いてみてください。",
    },
    # ── 中西藥交互作用 ──────────────────────────────────────────────────
    #
    # 與前兩種的差別：那兩種講「同一種作用吃了兩份」，這種講「兩套不同的用藥
    # 體系互相影響」。長輩常認為中藥溫和、不算「吃藥」，因此措辭要先破除
    # 「中藥可以配著吃」這個前提，再導向藥師。
    #
    # 資料來源必須標示：`flex.otc.source.tcm`。來源站自述「僅供藥師參考」，
    # 標明出處是讓收件人知道這則不是我們的判斷，而且查得到原文。
    #
    # 同一條紅線：SHALL NOT 給劑量建議、SHALL NOT 指示停藥。
    "flex.otc.header.tcm": {
        "zh-TW": "中藥與西藥併用提醒",
        "en": "Herbal and Western medicine together",
        "id": "Obat herbal dan obat Barat bersamaan",
        "vi": "Dùng thuốc Đông y cùng thuốc Tây",
        "th": "แจ้งเตือนใช้ยาจีนร่วมกับยาแผนปัจจุบัน",
        "ja": "漢方薬と西洋薬の併用について",
    },
    "flex.otc.alt.tcm": {
        "zh-TW": "{name} 的中西藥併用提醒",
        "en": "Herbal and Western medicine notice for {name}",
        "id": "Pemberitahuan obat herbal dan Barat untuk {name}",
        "vi": "Thông báo dùng chung Đông – Tây y của {name}",
        "th": "แจ้งเตือนใช้ยาจีนร่วมกับยาแผนปัจจุบันของ {name}",
        "ja": "{name} さんの漢方薬と西洋薬の併用のお知らせ",
    },
    "flex.otc.intro.tcm": {
        "zh-TW": "剛加入的這個藥，和已經在吃的藥有併用紀錄",
        "en": "just added this medication, which has a recorded interaction with one already being taken",
        "id": "baru menambahkan obat ini, yang tercatat berinteraksi dengan obat yang sedang diminum",
        "vi": "vừa thêm thuốc này, có ghi nhận tương tác với thuốc đang dùng",
        "th": "เพิ่งเพิ่มยานี้ ซึ่งมีบันทึกปฏิกิริยากับยาที่กินอยู่",
        "ja": "この薬を追加しました。すでに飲んでいる薬との併用の記録があります",
    },
    "flex.otc.label.tcm": {
        "zh-TW": "有併用紀錄的組合",
        "en": "Recorded combination",
        "id": "Kombinasi yang tercatat",
        "vi": "Cặp có ghi nhận",
        "th": "คู่ยาที่มีบันทึก",
        "ja": "記録のある組み合わせ",
    },
    "flex.otc.please_check.tcm": {
        "zh-TW": "中藥不等於溫和，它一樣會和西藥互相影響。這一組在衛福部的資料庫裡有併用紀錄。請找個時間一起看一下，或把兩種藥都拿給藥師確認——記得跟他說也有在吃中藥。當事人也收到了同一則提醒。",
        "en": "Herbal medicine is not automatically mild; it can still affect Western medicines. This combination is recorded in the Ministry of Health and Welfare database. Please take a moment to look at both together, or show both to a pharmacist — and mention the herbal medicine. They have received the same notice.",
        "id": "Obat herbal tidak otomatis ringan; obat ini tetap dapat memengaruhi obat Barat. Kombinasi ini tercatat dalam basis data Kementerian Kesehatan dan Kesejahteraan. Mohon lihat keduanya bersama, atau tunjukkan keduanya ke apoteker — dan sebutkan obat herbalnya. Yang bersangkutan juga menerima pemberitahuan yang sama.",
        "vi": "Thuốc Đông y không đương nhiên là nhẹ; nó vẫn có thể ảnh hưởng tới thuốc Tây. Cặp này có ghi nhận trong cơ sở dữ liệu của Bộ Y tế và Phúc lợi. Xin hãy cùng xem qua cả hai, hoặc mang cả hai đến hỏi dược sĩ — và nhớ nói rõ là có dùng thuốc Đông y. Người đó cũng đã nhận được thông báo tương tự.",
        "th": "ยาจีนไม่ได้แปลว่าอ่อนโยนเสมอไป ยังส่งผลต่อยาแผนปัจจุบันได้ คู่ยานี้มีบันทึกอยู่ในฐานข้อมูลของกระทรวงสาธารณสุขและสวัสดิการ กรุณาหาเวลาดูด้วยกัน หรือนำยาทั้งสองไปให้เภสัชกรตรวจสอบ และแจ้งด้วยว่ากินยาจีนอยู่ เจ้าตัวได้รับการแจ้งเตือนเดียวกันแล้ว",
        "ja": "漢方薬だから穏やかとは限らず、西洋薬に影響することがあります。この組み合わせは衛生福利部のデータベースに記録があります。お時間のあるときに一緒にご確認いただくか、両方を薬剤師にお見せください——漢方薬も飲んでいることをお伝えください。ご本人にも同じ通知が届いています。",
    },
    "flex.otc.source.tcm": {
        "zh-TW": "資料來源：衛生福利部中西藥交互作用資料庫",
        "en": "Source: MOHW Chinese–Western Drug Interaction Database",
        "id": "Sumber: Basis Data Interaksi Obat Tionghoa–Barat, MOHW",
        "vi": "Nguồn: Cơ sở dữ liệu tương tác Đông – Tây y, Bộ Y tế và Phúc lợi",
        "th": "แหล่งข้อมูล: ฐานข้อมูลปฏิกิริยายาจีน–ยาแผนปัจจุบัน กระทรวงสาธารณสุขและสวัสดิการ",
        "ja": "出典：衛生福利部 中西薬相互作用データベース",
    },
    "text.otc.patient.tcm": {
        "zh-TW": "你剛加入的「{new_drug}」，和已經在吃的「{existing_drug}」，在衛福部的資料庫裡有併用紀錄（{ingredients}）。\n\n很多人以為中藥比較溫和、可以配著吃，但它一樣會和西藥互相影響。\n\n這兩種不一定不能一起吃，但最好請藥師看一下。下次經過藥局時，把兩種藥都帶去問他，記得跟他說你也有在吃中藥。\n\n也讓家人幫你看一下，比較放心。",
        "en": "The 「{new_drug}」 you just added and 「{existing_drug}」, which you are already taking, have a recorded interaction ({ingredients}) in the Ministry of Health and Welfare database.\n\nMany people assume herbal medicine is mild and can be taken alongside anything, but it can still affect Western medicines.\n\nThat does not necessarily mean they cannot be taken together, but it is best to ask a pharmacist. Next time you pass a pharmacy, bring both and mention that you are taking herbal medicine.\n\nYour family can take a look with you too.",
        "id": "「{new_drug}」 yang baru Anda tambahkan dan 「{existing_drug}」 yang sedang Anda minum tercatat berinteraksi ({ingredients}) dalam basis data Kementerian Kesehatan dan Kesejahteraan.\n\nBanyak orang mengira obat herbal itu ringan dan bisa diminum bersama apa saja, padahal tetap dapat memengaruhi obat Barat.\n\nItu belum tentu berarti keduanya tidak boleh diminum bersama, tetapi sebaiknya tanyakan ke apoteker. Lain kali saat melewati apotek, bawalah keduanya dan sebutkan bahwa Anda minum obat herbal.\n\nKeluarga juga bisa ikut melihatnya bersama Anda.",
        "vi": "「{new_drug}」 bạn vừa thêm và 「{existing_drug}」 bạn đang dùng có ghi nhận tương tác ({ingredients}) trong cơ sở dữ liệu của Bộ Y tế và Phúc lợi.\n\nNhiều người nghĩ thuốc Đông y nhẹ nên uống chung được, nhưng nó vẫn ảnh hưởng tới thuốc Tây.\n\nĐiều đó không hẳn là không thể dùng chung, nhưng nên hỏi dược sĩ. Lần tới đi ngang nhà thuốc, hãy mang cả hai và nói rõ bạn có dùng thuốc Đông y.\n\nNgười nhà cũng có thể xem cùng bạn.",
        "th": "「{new_drug}」 ที่คุณเพิ่งเพิ่ม กับ 「{existing_drug}」 ที่คุณกินอยู่ มีบันทึกปฏิกิริยา ({ingredients}) ในฐานข้อมูลของกระทรวงสาธารณสุขและสวัสดิการ\n\nหลายคนคิดว่ายาจีนอ่อนโยนและกินร่วมกับอะไรก็ได้ แต่จริง ๆ แล้วยังส่งผลต่อยาแผนปัจจุบัน\n\nไม่ได้แปลว่ากินด้วยกันไม่ได้ แต่ควรถามเภสัชกร ครั้งหน้าที่ผ่านร้านยา นำยาทั้งสองไปถาม และบอกด้วยว่ากินยาจีนอยู่\n\nให้คนที่บ้านช่วยดูด้วยจะอุ่นใจกว่า",
        "ja": "追加された「{new_drug}」と、すでに飲んでいる「{existing_drug}」は、衛生福利部のデータベースに併用の記録（{ingredients}）があります。\n\n漢方薬は穏やかで何と一緒に飲んでも大丈夫と思われがちですが、西洋薬に影響することがあります。\n\n必ずしも一緒に飲めないわけではありませんが、薬剤師に見てもらうのが安心です。今度薬局に立ち寄ったとき、両方を持参し、漢方薬も飲んでいるとお伝えください。\n\nご家族にも一緒に見てもらいましょう。",
    },
    "text.otc.patient.tcm_solo": {
        "zh-TW": "你剛加入的「{new_drug}」，和已經在吃的「{existing_drug}」，在衛福部的資料庫裡有併用紀錄（{ingredients}）。\n\n很多人以為中藥比較溫和、可以配著吃，但它一樣會和西藥互相影響。\n\n這兩種不一定不能一起吃，但最好請藥師看一下。下次經過藥局時，把兩種藥都帶去問他，記得跟他說你也有在吃中藥。",
        "en": "The 「{new_drug}」 you just added and 「{existing_drug}」, which you are already taking, have a recorded interaction ({ingredients}) in the Ministry of Health and Welfare database.\n\nMany people assume herbal medicine is mild and can be taken alongside anything, but it can still affect Western medicines.\n\nThat does not necessarily mean they cannot be taken together, but it is best to ask a pharmacist. Next time you pass a pharmacy, bring both and mention that you are taking herbal medicine.",
        "id": "「{new_drug}」 yang baru Anda tambahkan dan 「{existing_drug}」 yang sedang Anda minum tercatat berinteraksi ({ingredients}) dalam basis data Kementerian Kesehatan dan Kesejahteraan.\n\nBanyak orang mengira obat herbal itu ringan dan bisa diminum bersama apa saja, padahal tetap dapat memengaruhi obat Barat.\n\nItu belum tentu berarti keduanya tidak boleh diminum bersama, tetapi sebaiknya tanyakan ke apoteker. Lain kali saat melewati apotek, bawalah keduanya dan sebutkan bahwa Anda minum obat herbal.",
        "vi": "「{new_drug}」 bạn vừa thêm và 「{existing_drug}」 bạn đang dùng có ghi nhận tương tác ({ingredients}) trong cơ sở dữ liệu của Bộ Y tế và Phúc lợi.\n\nNhiều người nghĩ thuốc Đông y nhẹ nên uống chung được, nhưng nó vẫn ảnh hưởng tới thuốc Tây.\n\nĐiều đó không hẳn là không thể dùng chung, nhưng nên hỏi dược sĩ. Lần tới đi ngang nhà thuốc, hãy mang cả hai và nói rõ bạn có dùng thuốc Đông y.",
        "th": "「{new_drug}」 ที่คุณเพิ่งเพิ่ม กับ 「{existing_drug}」 ที่คุณกินอยู่ มีบันทึกปฏิกิริยา ({ingredients}) ในฐานข้อมูลของกระทรวงสาธารณสุขและสวัสดิการ\n\nหลายคนคิดว่ายาจีนอ่อนโยนและกินร่วมกับอะไรก็ได้ แต่จริง ๆ แล้วยังส่งผลต่อยาแผนปัจจุบัน\n\nไม่ได้แปลว่ากินด้วยกันไม่ได้ แต่ควรถามเภสัชกร ครั้งหน้าที่ผ่านร้านยา นำยาทั้งสองไปถาม และบอกด้วยว่ากินยาจีนอยู่",
        "ja": "追加された「{new_drug}」と、すでに飲んでいる「{existing_drug}」は、衛生福利部のデータベースに併用の記録（{ingredients}）があります。\n\n漢方薬は穏やかで何と一緒に飲んでも大丈夫と思われがちですが、西洋薬に影響することがあります。\n\n必ずしも一緒に飲めないわけではありませんが、薬剤師に見てもらうのが安心です。今度薬局に立ち寄ったとき、両方を持参し、漢方薬も飲んでいるとお伝えください。",
    },
    # ── 出血風險（ATC 類別配對）──────────────────────────────────────────
    #
    # 四種通知裡唯一可能致命的，因此排在最前面。措辭與其餘三種的差別：
    # 這則要讓人**現在就注意身體徵兆**（黑便、牙齦出血、瘀青），那是當事人
    # 自己觀察得到、而且出現時必須立刻就醫的訊號。
    #
    # 同一條紅線：SHALL NOT 指示停藥。抗凝血劑自行停用會中風，比出血更嚴重
    # ——這正是「先別吃」在真正需要那顆藥的情況下本身就是傷害的典型例子。
    "flex.otc.header.bleeding": {
        "zh-TW": "出血風險提醒",
        "en": "Bleeding risk notice",
        "id": "Pemberitahuan risiko perdarahan",
        "vi": "Cảnh báo nguy cơ chảy máu",
        "th": "แจ้งเตือนความเสี่ยงเลือดออก",
        "ja": "出血リスクのお知らせ",
    },
    "flex.otc.alt.bleeding": {
        "zh-TW": "{name} 的出血風險提醒",
        "en": "Bleeding risk notice for {name}",
        "id": "Pemberitahuan risiko perdarahan untuk {name}",
        "vi": "Cảnh báo nguy cơ chảy máu của {name}",
        "th": "แจ้งเตือนความเสี่ยงเลือดออกของ {name}",
        "ja": "{name} さんの出血リスクのお知らせ",
    },
    "flex.otc.intro.bleeding": {
        "zh-TW": "剛加入的這個藥，和已經在吃的藥併用會增加出血風險",
        "en": "just added this medication, which raises bleeding risk alongside one already being taken",
        "id": "baru menambahkan obat ini, yang meningkatkan risiko perdarahan bersama obat yang sedang diminum",
        "vi": "vừa thêm thuốc này, dùng chung với thuốc đang uống sẽ tăng nguy cơ chảy máu",
        "th": "เพิ่งเพิ่มยานี้ ซึ่งใช้ร่วมกับยาที่กินอยู่จะเพิ่มความเสี่ยงเลือดออก",
        "ja": "この薬を追加しました。すでに飲んでいる薬との併用で出血リスクが高まります",
    },
    "flex.otc.label.bleeding": {
        "zh-TW": "併用的兩類藥",
        "en": "The two drug classes",
        "id": "Dua golongan obat",
        "vi": "Hai nhóm thuốc",
        "th": "ยาสองกลุ่ม",
        "ja": "併用している2つの薬効群",
    },
    "flex.otc.please_check.bleeding": {
        "zh-TW": "這兩類藥一起吃，腸胃道出血的機會會明顯增加。請留意有沒有黑便、牙齦出血、不明瘀青或異常疲倦，出現任何一項就要盡快就醫。請盡快帶著兩種藥去問藥師或原本開藥的醫師——但在問到之前，請不要自行停掉醫師開的藥。當事人也收到了同一則提醒。",
        "en": "Taking these two together clearly increases the chance of gastrointestinal bleeding. Watch for black stools, bleeding gums, unexplained bruising or unusual tiredness — see a doctor promptly if any appear. Please take both to a pharmacist or the prescribing doctor soon; until then, please do not stop the prescribed medication on your own. They have received the same notice.",
        "id": "Meminum keduanya bersama jelas meningkatkan risiko perdarahan saluran cerna. Perhatikan tinja hitam, gusi berdarah, memar tanpa sebab, atau lelah tidak biasa — segera ke dokter bila muncul. Mohon segera bawa keduanya ke apoteker atau dokter yang meresepkan; sampai saat itu, jangan hentikan sendiri obat resepnya. Yang bersangkutan juga menerima pemberitahuan yang sama.",
        "vi": "Dùng chung hai loại này rõ ràng làm tăng nguy cơ xuất huyết tiêu hóa. Hãy để ý phân đen, chảy máu chân răng, bầm tím không rõ nguyên nhân hoặc mệt bất thường — nếu có, đi khám ngay. Xin sớm mang cả hai đến hỏi dược sĩ hoặc bác sĩ đã kê đơn; trước khi hỏi được, đừng tự ý ngừng thuốc bác sĩ đã kê. Người đó cũng đã nhận được thông báo tương tự.",
        "th": "การกินสองอย่างนี้ร่วมกันเพิ่มโอกาสเลือดออกในทางเดินอาหารอย่างชัดเจน โปรดสังเกตอุจจาระสีดำ เลือดออกตามไรฟัน รอยช้ำไม่ทราบสาเหตุ หรืออ่อนเพลียผิดปกติ หากพบให้รีบพบแพทย์ กรุณานำยาทั้งสองไปถามเภสัชกรหรือแพทย์ผู้สั่งยาโดยเร็ว แต่ก่อนจะได้คำตอบ อย่าหยุดยาที่แพทย์สั่งเอง เจ้าตัวได้รับการแจ้งเตือนเดียวกันแล้ว",
        "ja": "この2つを一緒に飲むと、消化管出血の可能性が明らかに高まります。黒い便、歯ぐきからの出血、原因不明のあざ、いつもと違う疲れがないか気をつけ、いずれかが出たら早めに受診してください。できるだけ早く両方を薬剤師か処方した医師にお見せください。それまでは、処方された薬を自己判断でやめないでください。ご本人にも同じ通知が届いています。",
    },
    "text.otc.patient.bleeding": {
        "zh-TW": "你剛加入的「{new_drug}」，和已經在吃的「{existing_drug}」，是兩類一起吃會增加出血風險的藥（{ingredients}）。\n\n請留意這幾件事：大便變黑、刷牙流血、身上出現不明的瘀青、或是特別容易累。有任何一項就盡快去看醫生。\n\n請盡快帶著這兩種藥去問藥師，或回去問開藥給你的醫師。在問到之前，請不要自己停掉醫師開的藥——那樣可能更危險。\n\n也讓家人幫你看一下，比較放心。",
        "en": "The 「{new_drug}」 you just added and 「{existing_drug}」, which you are already taking, are two kinds of medicine that raise bleeding risk together ({ingredients}).\n\nPlease watch for black stools, bleeding when you brush your teeth, unexplained bruises, or feeling unusually tired. See a doctor promptly if any of these appear.\n\nPlease take both to a pharmacist soon, or go back to the doctor who prescribed for you. Until you have asked, do not stop the prescribed medication on your own — that can be more dangerous.\n\nYour family can take a look with you too.",
        "id": "「{new_drug}」 yang baru Anda tambahkan dan 「{existing_drug}」 yang sedang Anda minum adalah dua jenis obat yang bersama-sama meningkatkan risiko perdarahan ({ingredients}).\n\nMohon perhatikan: tinja menghitam, gusi berdarah saat menyikat gigi, memar tanpa sebab, atau mudah lelah. Segera ke dokter bila ada salah satunya.\n\nSegera bawa kedua obat ke apoteker, atau kembali ke dokter yang meresepkan. Sebelum bertanya, jangan hentikan sendiri obat resep dokter — itu bisa lebih berbahaya.\n\nKeluarga juga bisa ikut melihatnya bersama Anda.",
        "vi": "「{new_drug}」 bạn vừa thêm và 「{existing_drug}」 bạn đang dùng là hai loại thuốc khi dùng chung sẽ tăng nguy cơ chảy máu ({ingredients}).\n\nHãy để ý: phân đen, chảy máu khi đánh răng, vết bầm không rõ nguyên nhân, hoặc mệt bất thường. Nếu có bất kỳ dấu hiệu nào, hãy đi khám ngay.\n\nXin sớm mang cả hai loại đến hỏi dược sĩ, hoặc quay lại hỏi bác sĩ đã kê đơn. Trước khi hỏi được, đừng tự ý ngừng thuốc bác sĩ kê — như vậy có thể nguy hiểm hơn.\n\nNgười nhà cũng có thể xem cùng bạn.",
        "th": "「{new_drug}」 ที่คุณเพิ่งเพิ่ม กับ 「{existing_drug}」 ที่คุณกินอยู่ เป็นยาสองกลุ่มที่กินร่วมกันแล้วเพิ่มความเสี่ยงเลือดออก ({ingredients})\n\nโปรดสังเกต: อุจจาระสีดำ เลือดออกตอนแปรงฟัน รอยช้ำไม่ทราบสาเหตุ หรืออ่อนเพลียผิดปกติ หากมีอย่างใดอย่างหนึ่ง ให้รีบไปพบแพทย์\n\nกรุณานำยาทั้งสองไปถามเภสัชกรโดยเร็ว หรือกลับไปถามแพทย์ที่สั่งยาให้คุณ ก่อนจะได้คำตอบ อย่าหยุดยาที่แพทย์สั่งเอง เพราะอาจอันตรายกว่า\n\nให้คนที่บ้านช่วยดูด้วยจะอุ่นใจกว่า",
        "ja": "追加された「{new_drug}」と、すでに飲んでいる「{existing_drug}」は、一緒に飲むと出血しやすくなる2種類の薬です（{ingredients}）。\n\n黒い便、歯みがきのときの出血、身に覚えのないあざ、いつもより疲れやすい——このいずれかがあれば早めに受診してください。\n\nできるだけ早く両方を薬剤師に見せるか、処方した医師に相談してください。相談できるまで、処方された薬を自己判断でやめないでください。かえって危険なことがあります。\n\nご家族にも一緒に見てもらいましょう。",
    },
    "text.otc.patient.bleeding_solo": {
        "zh-TW": "你剛加入的「{new_drug}」，和已經在吃的「{existing_drug}」，是兩類一起吃會增加出血風險的藥（{ingredients}）。\n\n請留意這幾件事：大便變黑、刷牙流血、身上出現不明的瘀青、或是特別容易累。有任何一項就盡快去看醫生。\n\n請盡快帶著這兩種藥去問藥師，或回去問開藥給你的醫師。在問到之前，請不要自己停掉醫師開的藥——那樣可能更危險。",
        "en": "The 「{new_drug}」 you just added and 「{existing_drug}」, which you are already taking, are two kinds of medicine that raise bleeding risk together ({ingredients}).\n\nPlease watch for black stools, bleeding when you brush your teeth, unexplained bruises, or feeling unusually tired. See a doctor promptly if any of these appear.\n\nPlease take both to a pharmacist soon, or go back to the doctor who prescribed for you. Until you have asked, do not stop the prescribed medication on your own — that can be more dangerous.",
        "id": "「{new_drug}」 yang baru Anda tambahkan dan 「{existing_drug}」 yang sedang Anda minum adalah dua jenis obat yang bersama-sama meningkatkan risiko perdarahan ({ingredients}).\n\nMohon perhatikan: tinja menghitam, gusi berdarah saat menyikat gigi, memar tanpa sebab, atau mudah lelah. Segera ke dokter bila ada salah satunya.\n\nSegera bawa kedua obat ke apoteker, atau kembali ke dokter yang meresepkan. Sebelum bertanya, jangan hentikan sendiri obat resep dokter — itu bisa lebih berbahaya.",
        "vi": "「{new_drug}」 bạn vừa thêm và 「{existing_drug}」 bạn đang dùng là hai loại thuốc khi dùng chung sẽ tăng nguy cơ chảy máu ({ingredients}).\n\nHãy để ý: phân đen, chảy máu khi đánh răng, vết bầm không rõ nguyên nhân, hoặc mệt bất thường. Nếu có bất kỳ dấu hiệu nào, hãy đi khám ngay.\n\nXin sớm mang cả hai loại đến hỏi dược sĩ, hoặc quay lại hỏi bác sĩ đã kê đơn. Trước khi hỏi được, đừng tự ý ngừng thuốc bác sĩ kê — như vậy có thể nguy hiểm hơn.",
        "th": "「{new_drug}」 ที่คุณเพิ่งเพิ่ม กับ 「{existing_drug}」 ที่คุณกินอยู่ เป็นยาสองกลุ่มที่กินร่วมกันแล้วเพิ่มความเสี่ยงเลือดออก ({ingredients})\n\nโปรดสังเกต: อุจจาระสีดำ เลือดออกตอนแปรงฟัน รอยช้ำไม่ทราบสาเหตุ หรืออ่อนเพลียผิดปกติ หากมีอย่างใดอย่างหนึ่ง ให้รีบไปพบแพทย์\n\nกรุณานำยาทั้งสองไปถามเภสัชกรโดยเร็ว หรือกลับไปถามแพทย์ที่สั่งยาให้คุณ ก่อนจะได้คำตอบ อย่าหยุดยาที่แพทย์สั่งเอง เพราะอาจอันตรายกว่า",
        "ja": "追加された「{new_drug}」と、すでに飲んでいる「{existing_drug}」は、一緒に飲むと出血しやすくなる2種類の薬です（{ingredients}）。\n\n黒い便、歯みがきのときの出血、身に覚えのないあざ、いつもより疲れやすい——このいずれかがあれば早めに受診してください。\n\nできるだけ早く両方を薬剤師に見せるか、処方した医師に相談してください。相談できるまで、処方された薬を自己判断でやめないでください。かえって危険なことがあります。",
    },
    # ── 加好友歡迎卡 ─────────────────────────────────────────────────────
    #
    # 範例問句按下去會原樣送出，所以每種語言都要是使用者自己會打的句子，
    # 不是功能說明。挑題理由見 welcome_flex.py 開頭。
    "welcome.title": {
        "zh-TW": "歡迎使用 CARE 健康管家",
        "en": "Welcome to CARE Health Assistant",
        "id": "Selamat datang di CARE Asisten Kesehatan",
        "vi": "Chào mừng bạn đến với CARE Trợ lý Sức khỏe",
        "th": "ยินดีต้อนรับสู่ CARE ผู้ช่วยสุขภาพ",
        "ja": "CARE 健康アシスタントへようこそ",
    },
    "welcome.lead": {
        "zh-TW": "有健康上的疑問，直接傳訊息問我就可以。",
        "en": "Have a health question? Just send me a message.",
        "id": "Punya pertanyaan kesehatan? Langsung kirim pesan ke saya.",
        "vi": "Có thắc mắc về sức khỏe? Cứ nhắn tin hỏi tôi.",
        "th": "มีคำถามเรื่องสุขภาพ ส่งข้อความถามได้เลย",
        "ja": "健康について気になることは、そのままメッセージで聞いてください。",
    },
    "welcome.examples_title": {
        "zh-TW": "可以這樣問我",
        "en": "Try asking",
        "id": "Coba tanyakan",
        "vi": "Thử hỏi tôi",
        "th": "ลองถามแบบนี้",
        "ja": "たとえばこんな質問",
    },
    "welcome.examples_hint": {
        "zh-TW": "點一下就會送出",
        "en": "Tap one to send it",
        "id": "Ketuk untuk mengirim",
        "vi": "Chạm để gửi",
        "th": "แตะเพื่อส่ง",
        "ja": "タップすると送信されます",
    },
    "welcome.example.health": {
        "zh-TW": "血壓多少算高？",
        "en": "What blood pressure counts as high?",
        "id": "Tekanan darah berapa yang dianggap tinggi?",
        "vi": "Huyết áp bao nhiêu là cao?",
        "th": "ความดันเท่าไหร่ถึงเรียกว่าสูง?",
        "ja": "血圧はいくつから高いと言えますか？",
    },
    "welcome.example.rumor": {
        "zh-TW": "聽說紅豆的營養價值比牛肉還高，這是真的嗎？",
        "en": "I heard red beans are more nutritious than beef. Is that true?",
        "id": "Katanya kacang merah lebih bergizi daripada daging sapi, benarkah?",
        "vi": "Nghe nói đậu đỏ bổ dưỡng hơn thịt bò, có đúng không?",
        "th": "ได้ยินว่าถั่วแดงมีคุณค่าทางอาหารมากกว่าเนื้อวัว จริงไหม?",
        "ja": "小豆は牛肉より栄養価が高いって本当ですか？",
    },
    "welcome.example.nearby": {
        "zh-TW": "附近有哪些診所？",
        "en": "Are there any clinics nearby?",
        "id": "Ada klinik apa saja di dekat sini?",
        "vi": "Gần đây có phòng khám nào không?",
        "th": "แถวนี้มีคลินิกอะไรบ้าง?",
        "ja": "近くにどんなクリニックがありますか？",
    },
    "welcome.photo_hint": {
        "zh-TW": "也可以直接拍藥袋或藥品的照片傳給我。",
        "en": "You can also send me a photo of your medicine bag or pills.",
        "id": "Anda juga bisa mengirim foto kantong obat atau obat Anda.",
        "vi": "Bạn cũng có thể gửi ảnh túi thuốc hoặc viên thuốc cho tôi.",
        "th": "ส่งรูปถ่ายซองยาหรือตัวยามาให้ได้เช่นกัน",
        "ja": "お薬の袋や薬の写真を送ってもらうこともできます。",
    },
    "welcome.profile_button": {
        "zh-TW": "填寫我的健康資料",
        "en": "Fill in my health info",
        "id": "Isi data kesehatan saya",
        "vi": "Điền thông tin sức khỏe",
        "th": "กรอกข้อมูลสุขภาพ",
        "ja": "健康情報を入力する",
    },
    "welcome.profile_hint": {
        "zh-TW": "填好年齡、慢性病等資料，回答會更貼近你的狀況。語言和字體大小可以在「設定」調整。",
        "en": "Add your age, chronic conditions and more so answers fit your situation. Language and text size can be changed in Settings.",
        "id": "Isi usia, penyakit kronis, dan lainnya agar jawaban lebih sesuai dengan kondisi Anda. Bahasa dan ukuran huruf bisa diubah di Pengaturan.",
        "vi": "Điền tuổi, bệnh mãn tính… để câu trả lời sát với tình trạng của bạn hơn. Ngôn ngữ và cỡ chữ có thể đổi trong Cài đặt.",
        "th": "กรอกอายุ โรคประจำตัว ฯลฯ เพื่อให้คำตอบตรงกับสภาพของคุณมากขึ้น เปลี่ยนภาษาและขนาดตัวอักษรได้ในการตั้งค่า",
        "ja": "年齢や持病などを入力すると、あなたの状況に合った回答になります。言語と文字サイズは「設定」で変更できます。",
    },
    "welcome.disclaimer": {
        "zh-TW": "CARE 提供衛教資訊，不能取代醫師診斷。緊急狀況請撥 119。",
        "en": "CARE provides health information, not a medical diagnosis. In an emergency, call 119.",
        "id": "CARE memberikan informasi kesehatan, bukan diagnosis dokter. Dalam keadaan darurat, hubungi 119.",
        "vi": "CARE cung cấp thông tin sức khỏe, không thay thế chẩn đoán của bác sĩ. Khi khẩn cấp, hãy gọi 119.",
        "th": "CARE ให้ข้อมูลด้านสุขภาพ ไม่ใช่การวินิจฉัยของแพทย์ กรณีฉุกเฉินโทร 119",
        "ja": "CARE は健康情報を提供するもので、医師の診断に代わるものではありません。緊急時は 119 に電話してください。",
    },
}


def t(key: str, language: str | None = None) -> str:
    lang = get_request_language() if language is None else normalize_user_language(language)
    translations = _MESSAGES.get(key)
    if not translations:
        return key
    return translations.get(lang) or translations[DEFAULT_USER_LANGUAGE]


def department_label(department: str, language: str | None = None) -> str:
    """
    把資料庫的診療科別譯成使用者語言；沒有對應翻譯時原樣回傳。

    不能直接用 t()：t() 查不到 key 會回傳 key 本身，科別一旦沒收錄就會在卡片上
    顯示成「department.腸胃內科」。而 departments 除了部定專科之外，還混有次專科與
    「家醫科、內科、外科…」整串塞進單一元素的髒資料，這些本來就不可能全部收進字典，
    退回原文（中文）才是正確的降級——看得懂總比看到 key 好。
    """
    translations = _MESSAGES.get(f"department.{department}")
    if not translations:
        return department
    lang = get_request_language() if language is None else normalize_user_language(language)
    return translations.get(lang) or department


def all_sources_headings() -> frozenset[str]:
    return frozenset(t("agent.sources_heading", lang) for lang in SUPPORTED_LANGUAGES)


def all_rag_prefixes() -> frozenset[str]:
    return frozenset(t("agent.rag_prefix", lang) for lang in SUPPORTED_LANGUAGES)


def strip_rag_prefix(text: str) -> str:
    """剝除回覆首行的 RAG 前綴。

    卡片路徑不放前綴：前綴的職責是告知「這段內容有外部資料來源」，卡片以
    header 與可點的來源按鈕承擔同一職責，再放一行「以下為…」會與 header
    重複。純文字路徑仍需要它，因為那條路徑沒有任何其他標記。

    只剝除開頭：前綴字樣若出現在答案句中，那是內容的一部分，不能刪。
    """
    stripped = text.lstrip()
    for prefix in all_rag_prefixes():
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].lstrip()
    return text


def text_contains_sources_heading(text: str) -> bool:
    return any(heading in text for heading in all_sources_headings())


def split_at_sources_heading(text: str) -> tuple[str, str] | None:
    for heading in all_sources_headings():
        if heading in text:
            _, after = text.split(heading, 1)
            return heading, after
    return None


def strip_sources_section(text: str) -> str:
    split = split_at_sources_heading(text)
    if split is None:
        return text
    heading, _ = split
    before, _ = text.split(heading, 1)
    return before.rstrip()
