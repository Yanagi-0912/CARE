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
    # 逾時不是查無資料：使用者該做的是稍後再問，不是換個說法描述。
    "rag.fail.TIMEOUT": {
        "zh-TW": "這次查詢花太久，暫時沒有完成。請稍後再問一次。",
        "en": "This lookup took too long and could not be completed. Please try again later.",
        "id": "Pencarian ini memakan waktu terlalu lama dan belum selesai. Silakan coba lagi nanti.",
        "vi": "Lần tra cứu này mất quá nhiều thời gian và chưa hoàn tất. Vui lòng thử lại sau.",
        "th": "การค้นหาครั้งนี้ใช้เวลานานเกินไปและยังไม่เสร็จสิ้น กรุณาลองใหม่ภายหลัง",
        "ja": "今回の検索に時間がかかりすぎたため、完了できませんでした。しばらくしてから再度お試しください。",
    },
    "rag.fail.scam_notice": {
        "zh-TW": "如果這是要你匯款、轉帳或點連結的醫療訊息，請先不要照做，可以撥打 165 反詐騙諮詢專線查證。",
        "en": (
            "If this health-related message is asking you to send money or click a link, "
            "please don't. You can call the 165 anti-fraud hotline to check."
        ),
        "id": (
            "Jika pesan kesehatan ini meminta Anda mentransfer uang atau membuka tautan, "
            "jangan lakukan dulu. Anda bisa menelepon hotline anti-penipuan 165 untuk memastikan."
        ),
        "vi": (
            "Nếu tin nhắn y tế này yêu cầu bạn chuyển tiền hoặc bấm vào đường link, "
            "xin đừng làm theo. Bạn có thể gọi đường dây chống lừa đảo 165 để kiểm tra."
        ),
        "th": (
            "หากข้อความด้านสุขภาพนี้ขอให้คุณโอนเงินหรือกดลิงก์ อย่าเพิ่งทำตาม "
            "สามารถโทรสายด่วนต่อต้านการฉ้อโกง 165 เพื่อตรวจสอบได้"
        ),
        "ja": (
            "この医療に関するメッセージが送金やリンクのクリックを求めている場合は、従わないでください。"
            "165 詐欺相談専用ダイヤルで確認できます。"
        ),
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
    # --- 貼圖的簡短回覆 ----------------------------------------------------
    #
    # 依 LINE 附上的貼圖關鍵字挑一句（見 app/services/line_messaging/sticker_reply.py）。
    # 只是應答，不進 agent；對不上任何一類時回 fallback。
    "sticker.reply.thanks": {
        "zh-TW": "不客氣！有健康上的問題，隨時都可以問我。",
        "en": "You're welcome! Ask me about your health anytime.",
        "id": "Sama-sama! Tanyakan soal kesehatan Anda kapan saja.",
        "vi": "Không có gì! Bạn cứ hỏi tôi về sức khỏe bất cứ lúc nào.",
        "th": "ด้วยความยินดี! มีคำถามเรื่องสุขภาพ ถามได้ตลอดเลย",
        "ja": "どういたしまして！健康のことはいつでも聞いてくださいね。",
    },
    "sticker.reply.sorry": {
        "zh-TW": "沒關係，不用放在心上。",
        "en": "No worries at all.",
        "id": "Tidak apa-apa, jangan dipikirkan.",
        "vi": "Không sao đâu, bạn đừng bận tâm.",
        "th": "ไม่เป็นไรเลย ไม่ต้องคิดมาก",
        "ja": "大丈夫ですよ、気にしないでください。",
    },
    "sticker.reply.good_night": {
        "zh-TW": "晚安，早點休息，祝您好夢。",
        "en": "Good night. Get some rest and sleep well.",
        "id": "Selamat malam. Istirahatlah dan tidur yang nyenyak.",
        "vi": "Chúc ngủ ngon. Bạn nghỉ ngơi sớm nhé.",
        "th": "ราตรีสวัสดิ์ พักผ่อนเยอะ ๆ นอนหลับฝันดี",
        "ja": "おやすみなさい。ゆっくり休んでくださいね。",
    },
    "sticker.reply.good_morning": {
        "zh-TW": "早安！祝您今天精神好、身體健康。",
        "en": "Good morning! Wishing you a healthy, energetic day.",
        "id": "Selamat pagi! Semoga hari Anda sehat dan penuh semangat.",
        "vi": "Chào buổi sáng! Chúc bạn một ngày khỏe mạnh, tràn đầy năng lượng.",
        "th": "สวัสดีตอนเช้า! ขอให้วันนี้สุขภาพแข็งแรงและสดชื่น",
        "ja": "おはようございます！今日も元気に過ごせますように。",
    },
    "sticker.reply.bye": {
        "zh-TW": "掰掰，有需要隨時再來找我。",
        "en": "Bye! Come back anytime you need me.",
        "id": "Sampai jumpa! Hubungi saya kapan saja jika perlu.",
        "vi": "Tạm biệt! Cần gì cứ quay lại tìm tôi nhé.",
        "th": "แล้วพบกันใหม่! มีอะไรกลับมาหาได้ตลอด",
        "ja": "それではまた！必要なときはいつでも声をかけてください。",
    },
    "sticker.reply.greeting": {
        "zh-TW": "您好！有什麼健康上的問題，都可以問我喔。",
        "en": "Hello! Feel free to ask me any health question.",
        "id": "Halo! Silakan tanyakan apa saja tentang kesehatan.",
        "vi": "Xin chào! Bạn có câu hỏi nào về sức khỏe cứ hỏi tôi nhé.",
        "th": "สวัสดี! มีคำถามเรื่องสุขภาพอะไร ถามได้เลย",
        "ja": "こんにちは！健康のことなら何でも聞いてください。",
    },
    "sticker.reply.unwell": {
        "zh-TW": "怎麼了嗎？心情不好或身體不舒服，都可以跟我說。",
        "en": "Is something wrong? If you're feeling down or unwell, you can tell me.",
        "id": "Ada apa? Kalau sedang sedih atau kurang enak badan, ceritakan saja kepada saya.",
        "vi": "Có chuyện gì vậy? Nếu bạn buồn hay thấy không khỏe, cứ nói với tôi nhé.",
        "th": "เป็นอะไรหรือเปล่า? ถ้ารู้สึกไม่สบายใจหรือไม่สบายตัว บอกได้เลยนะ",
        "ja": "どうしましたか？気分が落ち込んでいたり体調が悪かったりしたら、教えてくださいね。",
    },
    "sticker.reply.love": {
        "zh-TW": "謝謝您，我也很關心您的健康！",
        "en": "Thank you! I care about your health too.",
        "id": "Terima kasih! Saya juga peduli dengan kesehatan Anda.",
        "vi": "Cảm ơn bạn! Tôi cũng luôn quan tâm đến sức khỏe của bạn.",
        "th": "ขอบคุณ! ฉันก็ใส่ใจสุขภาพของคุณเช่นกัน",
        "ja": "ありがとうございます！私もあなたの健康を大切に思っています。",
    },
    "sticker.reply.happy": {
        "zh-TW": "看到您開心，我也很開心！",
        "en": "Glad to see you happy!",
        "id": "Senang melihat Anda bahagia!",
        "vi": "Thấy bạn vui, tôi cũng vui lắm!",
        "th": "เห็นคุณมีความสุข ฉันก็ดีใจด้วย!",
        "ja": "楽しそうで、私もうれしいです！",
    },
    "sticker.reply.ok": {
        "zh-TW": "好的，收到！",
        "en": "Okay, got it!",
        "id": "Baik, sudah saya terima!",
        "vi": "Vâng, tôi đã nhận được!",
        "th": "ได้เลย รับทราบ!",
        "ja": "はい、承知しました！",
    },
    "sticker.reply.fallback": {
        "zh-TW": "收到您的貼圖了！有健康上的問題，可以直接打字或傳語音問我。",
        "en": "Got your sticker! If you have a health question, just type it or send a voice message.",
        "id": "Stiker Anda sudah saya terima! Kalau ada pertanyaan kesehatan, ketik saja atau kirim pesan suara.",
        "vi": "Tôi đã nhận được sticker của bạn! Nếu có câu hỏi về sức khỏe, bạn cứ nhắn chữ hoặc gửi tin nhắn thoại nhé.",
        "th": "ได้รับสติกเกอร์แล้ว! ถ้ามีคำถามเรื่องสุขภาพ พิมพ์หรือส่งข้อความเสียงมาได้เลย",
        "ja": "スタンプを受け取りました！健康について聞きたいことがあれば、文字か音声で送ってください。",
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
    # 列表裡沒有一家登記所查科別時的標題（附近全是沒登記專科的診所）。長輩可能只看
    # 標題不看副標，因此標題本身就要揭露「沒有搜尋到該科診所」，不能只寫搜尋條件。
    "location.department.title_unspecified": {
        "zh-TW": "附近沒有搜尋到「{department}」診所",
        "en": 'No nearby clinic found for "{department}"',
        "id": 'Tidak ditemukan klinik "{department}" di sekitar Anda',
        "vi": 'Không tìm thấy phòng khám "{department}" gần đây',
        "th": 'ไม่พบคลินิก "{department}" ใกล้เคียง',
        "ja": "近くに「{department}」の診療所は見つかりませんでした",
    },
    "location.department.all_unspecified": {
        "zh-TW": (
            "※ 下面 {count} 間都沒有登記{department}。原因是健保院所資料只標示它們"
            "未登記專科（多為一般門診），系統因為距離近而一併列出；是否有看"
            "{department}，請先去電確認。"
        ),
        "en": (
            "※ None of the {count} below list {department}: they have no specialty "
            "registered in the NHI facility data (mostly general practice) and are "
            "shown because they are closest to you. Please call ahead to check "
            "whether they see {department} patients."
        ),
        "id": (
            "※ Tidak satu pun dari {count} fasilitas di bawah mencantumkan {department}: "
            "dalam data fasilitas NHI mereka tidak memiliki spesialisasi terdaftar "
            "(umumnya praktik umum) dan ditampilkan karena paling dekat dengan Anda. "
            "Sebaiknya telepon dulu untuk memastikan layanan {department}."
        ),
        "vi": (
            "※ Cả {count} cơ sở bên dưới đều không ghi {department}: trong dữ liệu "
            "cơ sở y tế NHI, họ không đăng ký chuyên khoa (phần lớn là khám tổng quát) "
            "và được hiển thị vì gần bạn nhất. Vui lòng gọi trước để hỏi có khám "
            "{department} không."
        ),
        "th": (
            "※ สถานพยาบาลทั้ง {count} แห่งด้านล่างไม่ได้ระบุ{department}: "
            "ในข้อมูลสถานพยาบาล NHI ไม่ได้ลงทะเบียนแผนกเฉพาะทาง (ส่วนใหญ่เป็นการตรวจโรคทั่วไป) "
            "และแสดงเพราะอยู่ใกล้คุณที่สุด แนะนำให้โทรสอบถามก่อนว่ามีบริการ{department}หรือไม่"
        ),
        "ja": (
            "※ 以下の {count} 件はいずれも{department}の登録がありません。健保の医療機関"
            "データで専門科の登録がなく（主に一般診療）、最も近いため表示しています。"
            "{department}を受診できるか、事前に電話でご確認ください。"
        ),
    },
    # 使用者說的科別在健保資料裡不存在時（例如腸胃科屬於內科），必須誠實說明這層
    # 對應，否則使用者會以為系統真的找到了腸胃專科。句尾寫「依…搜尋」而非「以下為
    # …院所」：搜內科會一併列出沒登記專科的一般門診，不能保證每一家都是內科。
    "location.department.alias_note": {
        "zh-TW": "※「{requested}」在健保院所資料中歸類於「{canonical}」，以下依{canonical}搜尋。",
        "en": '※ "{requested}" is classified under "{canonical}" in the NHI facility data. Results below are from a {canonical} search.',
        "id": '※ "{requested}" diklasifikasikan sebagai "{canonical}" dalam data fasilitas NHI. Hasil di bawah berdasarkan pencarian {canonical}.',
        "vi": '※ "{requested}" được xếp vào "{canonical}" trong dữ liệu cơ sở y tế NHI. Kết quả bên dưới được tìm theo {canonical}.',
        "th": "※ \"{requested}\" ถูกจัดอยู่ในหมวด \"{canonical}\" ในข้อมูลสถานพยาบาล NHI ผลลัพธ์ด้านล่างค้นหาตาม{canonical}",
        "ja": "※「{requested}」は健保の医療機関データでは「{canonical}」に分類されます。以下は{canonical}で検索した結果です。",
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
    # 要求營業中、結果全是急診院所時（深夜幾乎都是這樣）。卡片上的門診狀態會是
    # 「今日已結束」，副標若講「目前營業中」會互相矛盾；資料也沒說急診幾點開。
    "location.open_now.emergency_only": {
        "zh-TW": "附近診所現在都沒有看診，以下是最近 {count} 家設有急診的院所",
        "en": "No clinics nearby are seeing patients right now. Below are the {count} nearest facilities with an emergency department.",
        "id": "Tidak ada klinik terdekat yang melayani pasien saat ini. Berikut {count} fasilitas terdekat yang memiliki unit gawat darurat.",
        "vi": "Hiện không có phòng khám nào gần đây đang khám bệnh. Dưới đây là {count} cơ sở gần nhất có khoa cấp cứu.",
        "th": "ขณะนี้ไม่มีคลินิกใกล้เคียงที่เปิดตรวจ ด้านล่างคือสถานพยาบาลที่มีแผนกฉุกเฉินที่ใกล้ที่สุด {count} แห่ง",
        "ja": "現在診療中の近くのクリニックはありません。以下は救急外来のある最寄りの医療機関 {count} 件です。",
    },
    # 深夜使用者沒講「現在有開的」、是系統自動只列現在能去的時候，接在副標後面。
    # 要說出來，也要告訴使用者想找明天看診的該怎麼問（講科別就不會自動篩）。
    "location.open_now.late_night_note": {
        "zh-TW": "※ 現在是深夜，只列出現在就能去的院所；想找明天看診的，請告訴我要看哪一科。",
        "en": "※ It's late at night, so only places you can go to right now are listed. To find a clinic for tomorrow, tell me which specialty you need.",
        "id": "※ Saat ini sudah larut malam, jadi hanya fasilitas yang bisa Anda datangi sekarang yang ditampilkan. Untuk mencari klinik besok, beri tahu saya spesialisasi yang Anda butuhkan.",
        "vi": "※ Bây giờ đã khuya nên chỉ liệt kê những nơi có thể đến ngay. Nếu muốn tìm phòng khám cho ngày mai, hãy cho tôi biết bạn cần khám chuyên khoa nào.",
        "th": "※ ตอนนี้ดึกแล้ว จึงแสดงเฉพาะสถานพยาบาลที่ไปได้ทันที หากต้องการหาคลินิกสำหรับพรุ่งนี้ โปรดบอกว่าต้องการตรวจแผนกใด",
        "ja": "※ 深夜のため、今すぐ行ける医療機関のみ表示しています。明日受診できるクリニックを探す場合は、診療科を教えてください。",
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
    # 一次搜多科、其中幾科看不懂時：照查看得懂的，但要說清楚哪幾科沒被搜尋，
    # 否則使用者會以為每一科都查過了。
    "location.department.partial_unknown": {
        "zh-TW": "※ 我不確定「{unresolved}」對應到哪一個科別，以下只列出{searched}的院所。",
        "en": '※ I am not sure which specialty "{unresolved}" maps to, so only {searched} facilities are listed below.',
        "id": '※ Saya tidak yakin "{unresolved}" termasuk spesialisasi apa, jadi hanya fasilitas {searched} yang ditampilkan di bawah.',
        "vi": '※ Tôi không chắc "{unresolved}" thuộc chuyên khoa nào, nên bên dưới chỉ liệt kê các cơ sở {searched}.',
        "th": "※ ฉันไม่แน่ใจว่า \"{unresolved}\" ตรงกับแผนกใด จึงแสดงเฉพาะสถานพยาบาล{searched}ด้านล่าง",
        "ja": "※「{unresolved}」がどの診療科に該当するか判断できなかったため、以下は{searched}の医療機関のみです。",
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
    # --- Flex：分享 CARE（官方帳號 QR＋邀請家人） ---
    "flex.share.title": {
        "zh-TW": "邀請朋友一起用 CARE",
        "en": "Invite friends to CARE",
        "id": "Ajak teman memakai CARE",
        "vi": "Mời bạn bè cùng dùng CARE",
        "th": "ชวนเพื่อนมาใช้ CARE",
        "ja": "友だちを CARE に招待",
    },
    "flex.share.desc": {
        "zh-TW": "請朋友用 LINE 掃這個 QR code，或按下面的按鈕把 CARE 傳給他。",
        "en": "Ask your friend to scan this QR code with LINE, or tap the button below to send CARE to them.",
        "id": "Minta teman memindai kode QR ini dengan LINE, atau ketuk tombol di bawah untuk mengirim CARE kepadanya.",
        "vi": "Nhờ bạn bè quét mã QR này bằng LINE, hoặc nhấn nút bên dưới để gửi CARE cho họ.",
        "th": "ให้เพื่อนสแกน QR code นี้ด้วย LINE หรือแตะปุ่มด้านล่างเพื่อส่ง CARE ให้เพื่อน",
        "ja": "友だちに LINE でこの QR コードを読み取ってもらうか、下のボタンで CARE を送ってください。",
    },
    "flex.share.button": {
        "zh-TW": "分享給 LINE 好友",
        "en": "Share with LINE friends",
        "id": "Bagikan ke teman LINE",
        "vi": "Chia sẻ cho bạn bè LINE",
        "th": "แชร์ให้เพื่อนใน LINE",
        "ja": "LINE の友だちに送る",
    },
    "flex.share.family_prompt": {
        "zh-TW": "想讓家人看到你的用藥？",
        "en": "Want your family to see your medications?",
        "id": "Ingin keluarga bisa melihat obat Anda?",
        "vi": "Muốn người thân xem được thuốc của bạn?",
        "th": "อยากให้ครอบครัวเห็นยาที่คุณใช้ไหม",
        "ja": "家族にお薬の状況を見てもらいませんか？",
    },
    "flex.share.family_button": {
        "zh-TW": "邀請家人加入我的家庭",
        "en": "Invite family members",
        "id": "Undang anggota keluarga",
        "vi": "Mời người thân vào gia đình",
        "th": "ชวนคนในครอบครัว",
        "ja": "家族を招待する",
    },
    # 分享卡後面那則純文字：卡片裡的字不能長按複製，這則才能複製、轉貼。
    "share.link_text": {
        "zh-TW": "CARE 加好友連結（長按可以複製、轉傳）：\n{url}",
        "en": "CARE add-friend link (long-press to copy or forward):\n{url}",
        "id": "Tautan tambah teman CARE (tekan lama untuk menyalin atau meneruskan):\n{url}",
        "vi": "Liên kết kết bạn với CARE (nhấn giữ để sao chép hoặc chuyển tiếp):\n{url}",
        "th": "ลิงก์เพิ่มเพื่อน CARE (กดค้างเพื่อคัดลอกหรือส่งต่อ):\n{url}",
        "ja": "CARE の友だち追加リンク（長押しでコピー・転送できます）：\n{url}",
    },
    "share.unavailable": {
        "zh-TW": "分享連結暫時拿不到，請稍後再試。",
        "en": "The share link isn't available right now. Please try again later.",
        "id": "Tautan berbagi belum bisa diambil. Silakan coba lagi nanti.",
        "vi": "Hiện chưa lấy được liên kết chia sẻ. Vui lòng thử lại sau.",
        "th": "ยังไม่สามารถดึงลิงก์แชร์ได้ในขณะนี้ กรุณาลองใหม่ภายหลัง",
        "ja": "共有リンクを取得できませんでした。しばらくしてからもう一度お試しください。",
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
        "zh-TW": (
            "此院所資料未載明科別（屬一般門診），因離您近而一併列出，"
            "不一定有您要找的科別，建議先去電確認。"
        ),
        "en": (
            "This facility lists no specialty (general practice). It is shown "
            "because it is nearby and may not offer the specialty you need. "
            "Please call ahead to confirm."
        ),
        "id": (
            "Fasilitas ini tidak mencantumkan spesialisasi (praktik umum). "
            "Ditampilkan karena lokasinya dekat dan belum tentu memiliki "
            "spesialisasi yang Anda cari. Sebaiknya telepon dulu untuk memastikan."
        ),
        "vi": (
            "Cơ sở này không ghi chuyên khoa (khám tổng quát). Được hiển thị vì "
            "ở gần bạn, có thể không có chuyên khoa bạn cần. "
            "Vui lòng gọi trước để xác nhận."
        ),
        "th": (
            "สถานพยาบาลนี้ไม่ได้ระบุแผนก (ตรวจโรคทั่วไป) แสดงเพราะอยู่ใกล้คุณ "
            "อาจไม่มีแผนกที่คุณต้องการ แนะนำให้โทรสอบถามก่อน"
        ),
        "ja": (
            "この医療機関は診療科の記載がありません（一般診療）。"
            "お近くのため表示していますが、お探しの診療科がない場合があります。"
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
    # 進診間前按這裡，直接開到錄音頁。按鈕字數受 _POSTBACK_LABEL_MAX 限制，
    # 各語言都要短——長輩在診間門口沒有時間讀完一句話。
    "flex.appt.button.record": {
        "zh-TW": "看診時錄音",
        "en": "Record the visit",
        "id": "Rekam kunjungan",
        "vi": "Ghi âm buổi khám",
        "th": "บันทึกเสียงการตรวจ",
        "ja": "診察を録音",
    },
    # 看診錄音整理完成／失敗的推播（app/services/clinic_transcript/notifier.py）。
    # 2026-09-22 起卡片直接放摘要（James 決定本人與家人都在聊天室看），
    # 這幾句只是卡片開頭那一行。
    "flex.clinic.ready.header": {
        "zh-TW": "看診錄音整理好了",
        "en": "Visit recording is ready",
        "id": "Rekaman kunjungan sudah siap",
        "vi": "Bản ghi buổi khám đã xong",
        "th": "บันทึกการตรวจพร้อมแล้ว",
        "ja": "診察の録音がまとまりました",
    },
    "flex.clinic.ready.body.self": {
        "zh-TW": "這次看診的重點整理如下。",
        "en": "Here are the key points from your visit.",
        "id": "Berikut poin penting dari kunjungan Anda.",
        "vi": "Đây là các điểm chính của buổi khám.",
        "th": "สรุปประเด็นสำคัญจากการตรวจครั้งนี้",
        "ja": "今回の診察の要点をまとめました。",
    },
    "flex.clinic.ready.body.family": {
        "zh-TW": "{name}這次看診的重點整理如下。",
        "en": "Here are the key points from {name}'s visit.",
        "id": "Berikut poin penting dari kunjungan {name}.",
        "vi": "Đây là các điểm chính của buổi khám của {name}.",
        "th": "สรุปประเด็นสำคัญจากการตรวจของ {name}",
        "ja": "{name}さんの今回の診察の要点をまとめました。",
    },
    "flex.clinic.failed.header": {
        "zh-TW": "看診錄音沒有整理成功",
        "en": "Visit recording couldn't be processed",
        "id": "Rekaman kunjungan gagal diproses",
        "vi": "Không xử lý được bản ghi buổi khám",
        "th": "ประมวลผลบันทึกการตรวจไม่สำเร็จ",
        "ja": "診察の録音をまとめられませんでした",
    },
    "flex.clinic.failed.body": {
        "zh-TW": "可能是聲音太小或錄音中斷。下次看診可以再錄一次。",
        "en": "The sound may have been too quiet or the recording was cut off. You can record again at your next visit.",
        "id": "Mungkin suaranya terlalu pelan atau rekaman terputus. Anda bisa merekam lagi di kunjungan berikutnya.",
        "vi": "Có thể âm thanh quá nhỏ hoặc bản ghi bị gián đoạn. Lần khám sau bạn có thể ghi âm lại.",
        "th": "เสียงอาจเบาเกินไปหรือการบันทึกถูกตัด ครั้งหน้าที่ไปตรวจสามารถบันทึกใหม่ได้",
        "ja": "音が小さすぎたか、録音が途中で切れた可能性があります。次の診察でもう一度録音できます。",
    },
    "flex.clinic.button.open": {
        "zh-TW": "看完整逐字稿",
        "en": "Full transcript",
        "id": "Transkrip lengkap",
        "vi": "Xem toàn văn",
        "th": "ดูข้อความทั้งหมด",
        "ja": "全文を見る",
    },
    # 摘要卡片的小標（clinic_visit_flex）。欄位名稱避開說話者，理由見 summarizer。
    "flex.clinic.section.main_points": {
        "zh-TW": "重點",
        "en": "Key points",
        "id": "Poin penting",
        "vi": "Điểm chính",
        "th": "ประเด็นสำคัญ",
        "ja": "要点",
    },
    "flex.clinic.section.medication_changes": {
        "zh-TW": "用藥有變動",
        "en": "Medication changes",
        "id": "Perubahan obat",
        "vi": "Thay đổi thuốc",
        "th": "การเปลี่ยนแปลงยา",
        "ja": "お薬の変更",
    },
    # 用藥變動後面附的逐字稿原文，讓讀的人能當場核對（summarizer 規則 4）。
    "flex.clinic.quote": {
        "zh-TW": "原話：「{quote}」",
        "en": "Original words: \"{quote}\"",
        "id": "Kata aslinya: \"{quote}\"",
        "vi": "Nguyên văn: \"{quote}\"",
        "th": "คำพูดเดิม: \"{quote}\"",
        "ja": "元の言葉：「{quote}」",
    },
    "flex.clinic.section.next_visit": {
        "zh-TW": "下次回診",
        "en": "Next visit",
        "id": "Kunjungan berikutnya",
        "vi": "Lần khám tới",
        "th": "นัดครั้งถัดไป",
        "ja": "次回の受診",
    },
    "flex.clinic.section.reminders": {
        "zh-TW": "要記得",
        "en": "Remember",
        "id": "Ingat",
        "vi": "Cần nhớ",
        "th": "สิ่งที่ต้องจำ",
        "ja": "覚えておくこと",
    },
    "flex.clinic.section.unclear": {
        "zh-TW": "沒聽清楚、要再問醫師的",
        "en": "Unclear — ask the doctor again",
        "id": "Kurang jelas — tanyakan lagi ke dokter",
        "vi": "Chưa rõ — nên hỏi lại bác sĩ",
        "th": "ฟังไม่ชัด ควรถามแพทย์อีกครั้ง",
        "ja": "聞き取れなかったこと（医師に再確認）",
    },
    "flex.clinic.self_recap_note": {
        "zh-TW": "這是出診間後自己講的版本，不是醫師的原話。",
        "en": "This is what was recalled after the visit, not the doctor's own words.",
        "id": "Ini versi yang diceritakan ulang setelah kunjungan, bukan kata-kata dokter langsung.",
        "vi": "Đây là phần tự kể lại sau buổi khám, không phải lời bác sĩ.",
        "th": "นี่คือสิ่งที่เล่าซ้ำหลังออกจากห้องตรวจ ไม่ใช่คำพูดของแพทย์โดยตรง",
        "ja": "これは診察後に本人が話した内容で、医師の言葉そのものではありません。",
    },
    "flex.clinic.empty_summary": {
        "zh-TW": "這次沒有整理出摘要，可以直接看逐字稿。",
        "en": "No summary this time. You can read the transcript directly.",
        "id": "Kali ini tidak ada ringkasan. Anda bisa langsung membaca transkripnya.",
        "vi": "Lần này không có tóm tắt. Bạn có thể đọc toàn văn.",
        "th": "ครั้งนี้ไม่มีสรุป สามารถอ่านข้อความทั้งหมดได้เลย",
        "ja": "今回は要約がありません。全文をご覧ください。",
    },
    # ---- 在 LINE 聊天室錄看診（app/services/clinic_transcript/line_flow.py）----
    # 衛福部《醫療機構醫療隱私維護規範》第二點：錄音前要先徵得醫師同意。
    "clinic.chat.ask_consent": {
        "zh-TW": "要錄這次看診嗎？錄之前請先問醫師：「可以錄音嗎？我怕回家記不住。」\n醫師同意就錄診間對話；不同意的話，出診間後你自己把醫師說的講一遍，一樣幫你整理。",
        "en": "Want to record this visit? Please ask the doctor first: \"May I record this? I'm afraid I'll forget.\"\nIf the doctor agrees, record the visit. If not, after the visit just say what the doctor told you, and we'll organize that instead.",
        "id": "Mau merekam kunjungan ini? Tanyakan dulu ke dokter: \"Boleh saya rekam? Saya takut lupa.\"\nJika dokter setuju, rekam kunjungannya. Jika tidak, setelah keluar ceritakan saja apa yang dikatakan dokter, kami tetap merangkumnya.",
        "vi": "Bạn muốn ghi âm buổi khám này? Hãy hỏi bác sĩ trước: \"Tôi ghi âm được không? Tôi sợ quên.\"\nNếu bác sĩ đồng ý thì ghi âm buổi khám. Nếu không, sau khi khám bạn tự kể lại lời bác sĩ, chúng tôi vẫn tóm tắt giúp.",
        "th": "ต้องการบันทึกเสียงการตรวจครั้งนี้ไหม? กรุณาถามแพทย์ก่อน: \"ขออัดเสียงได้ไหมคะ/ครับ กลัวลืม\"\nถ้าแพทย์ยินยอมก็บันทึกได้เลย ถ้าไม่ยินยอม หลังออกจากห้องตรวจให้เล่าสิ่งที่แพทย์บอกแทน เราจะสรุปให้เหมือนกัน",
        "ja": "今回の診察を録音しますか？先に医師に「忘れそうなので録音してもいいですか」と聞いてください。\n同意があれば診察を録音します。だめな場合は、診察後に医師の話をご自分で話してください。同じようにまとめます。",
    },
    "clinic.chat.ask_is_visit": {
        "zh-TW": "這段錄音比較長，要幫你整理成看診紀錄嗎？錄的時候醫師有同意嗎？",
        "en": "This recording is long. Should we turn it into a visit record? Did the doctor agree to the recording?",
        "id": "Rekaman ini cukup panjang. Mau dijadikan catatan kunjungan? Apakah dokter setuju direkam?",
        "vi": "Bản ghi này khá dài. Bạn muốn lưu thành ghi chép buổi khám không? Bác sĩ có đồng ý ghi âm không?",
        "th": "เสียงนี้ค่อนข้างยาว ต้องการให้สรุปเป็นบันทึกการตรวจไหม? ตอนบันทึกแพทย์ยินยอมหรือเปล่า?",
        "ja": "長めの録音です。診察の記録としてまとめますか？録音について医師の同意はありましたか？",
    },
    # 快速回覆的按鈕字：LINE 上限 20 字。
    "clinic.chat.qr.doctor_agreed": {
        "zh-TW": "醫師同意錄音",
        "en": "Doctor agreed",
        "id": "Dokter setuju",
        "vi": "Bác sĩ đồng ý",
        "th": "แพทย์ยินยอม",
        "ja": "医師が同意",
    },
    "clinic.chat.qr.self_recap": {
        "zh-TW": "我出來自己講",
        "en": "I'll recap myself",
        "id": "Saya ceritakan ulang",
        "vi": "Tôi tự kể lại",
        "th": "ฉันจะเล่าเอง",
        "ja": "自分で話す",
    },
    "clinic.chat.qr.cancel": {
        "zh-TW": "不錄了",
        "en": "Cancel",
        "id": "Batal",
        "vi": "Hủy",
        "th": "ยกเลิก",
        "ja": "やめる",
    },
    "clinic.chat.qr.not_visit": {
        "zh-TW": "不是看診錄音",
        "en": "Not a visit",
        "id": "Bukan kunjungan",
        "vi": "Không phải buổi khám",
        "th": "ไม่ใช่การตรวจ",
        "ja": "診察ではない",
    },
    "clinic.chat.instructions.doctor_agreed": {
        "zh-TW": "好！看診時按下方的麥克風開始錄音，看完再送出。\n也可以用手機的錄音程式錄，錄完分享到這裡。",
        "en": "OK! During the visit, tap the microphone below to record, and send it when you're done.\nYou can also use your phone's recorder app and share the file here.",
        "id": "Baik! Saat diperiksa, ketuk mikrofon di bawah untuk merekam, lalu kirim setelah selesai.\nBisa juga pakai aplikasi perekam di ponsel, lalu bagikan filenya ke sini.",
        "vi": "Được! Khi khám, nhấn micro bên dưới để ghi âm, xong thì gửi.\nBạn cũng có thể dùng ứng dụng ghi âm của điện thoại rồi chia sẻ tệp vào đây.",
        "th": "ได้เลย! ระหว่างตรวจ กดไมโครโฟนด้านล่างเพื่อบันทึก เสร็จแล้วกดส่ง\nหรือใช้แอปบันทึกเสียงในมือถือแล้วแชร์ไฟล์มาที่นี่ก็ได้",
        "ja": "わかりました！診察中は下のマイクを押して録音し、終わったら送信してください。\nスマホの録音アプリで録って、ここに共有しても大丈夫です。",
    },
    "clinic.chat.instructions.self_recap": {
        "zh-TW": "好！出診間後，按下方的麥克風，把醫師說的講一遍再送出，例如藥怎麼吃、下次什麼時候回診。",
        "en": "OK! After the visit, tap the microphone below and say what the doctor told you, like how to take the medicine and when to come back, then send it.",
        "id": "Baik! Setelah keluar, ketuk mikrofon di bawah dan ceritakan apa kata dokter, misalnya cara minum obat dan kapan kontrol lagi, lalu kirim.",
        "vi": "Được! Sau khi khám, nhấn micro bên dưới và kể lại lời bác sĩ, ví dụ cách uống thuốc, khi nào tái khám, rồi gửi.",
        "th": "ได้เลย! หลังออกจากห้องตรวจ กดไมโครโฟนด้านล่างแล้วเล่าสิ่งที่แพทย์บอก เช่น กินยาอย่างไร นัดครั้งหน้าเมื่อไร แล้วกดส่ง",
        "ja": "わかりました！診察後に下のマイクを押して、薬の飲み方や次回の受診日など医師の話を話してから送信してください。",
    },
    "clinic.chat.received": {
        "zh-TW": "收到了，正在整理。好了會傳到這裡，有權限的家人也會收到。講台語比較多的話要等久一點。",
        "en": "Got it, we're working on it. We'll send it here when it's ready, and family members with access will get it too.",
        "id": "Sudah diterima, sedang diproses. Hasilnya akan dikirim ke sini, dan keluarga yang punya akses juga akan menerimanya.",
        "vi": "Đã nhận, đang xử lý. Khi xong sẽ gửi vào đây, người nhà có quyền xem cũng sẽ nhận được.",
        "th": "ได้รับแล้ว กำลังสรุปให้ เสร็จแล้วจะส่งมาที่นี่ และครอบครัวที่มีสิทธิ์ดูก็จะได้รับด้วย",
        "ja": "受け取りました。まとめています。できたらここに送ります。閲覧権限のあるご家族にも届きます。",
    },
    "clinic.chat.cancelled": {
        "zh-TW": "好，這次不錄。",
        "en": "OK, no recording this time.",
        "id": "Baik, kali ini tidak direkam.",
        "vi": "Được, lần này không ghi âm.",
        "th": "ได้ ครั้งนี้ไม่บันทึก",
        "ja": "わかりました。今回は録音しません。",
    },
    "clinic.chat.not_visit": {
        "zh-TW": "好，這段就不整理。如果是想問問題，可以再傳一段短一點的語音。",
        "en": "OK, we won't process it. If you have a question, send a shorter voice message.",
        "id": "Baik, tidak diproses. Jika ingin bertanya, kirim pesan suara yang lebih pendek.",
        "vi": "Được, sẽ không xử lý. Nếu muốn hỏi, hãy gửi một tin nhắn thoại ngắn hơn.",
        "th": "ได้ จะไม่สรุปเสียงนี้ ถ้าต้องการถามคำถาม ส่งข้อความเสียงที่สั้นกว่านี้มาได้เลย",
        "ja": "わかりました。まとめません。質問があれば、短めの音声を送ってください。",
    },
    "clinic.chat.expired": {
        "zh-TW": "這次的看診錄音已經過期了。要錄的話，請再傳一次「看診錄音」。",
        "en": "This recording request has expired. To record, send \"record visit\" again.",
        "id": "Permintaan rekaman ini sudah kedaluwarsa. Untuk merekam, kirim \"rekam kunjungan\" lagi.",
        "vi": "Yêu cầu ghi âm này đã hết hạn. Muốn ghi âm, hãy gửi lại \"ghi âm buổi khám\".",
        "th": "คำขอบันทึกนี้หมดอายุแล้ว ถ้าต้องการบันทึก ส่ง \"บันทึกการตรวจ\" อีกครั้ง",
        "ja": "この録音の受付は期限切れです。録音する場合はもう一度「診察を録音」と送ってください。",
    },
    "clinic.chat.too_large": {
        "zh-TW": "錄音檔太大了（超過 {limit_mb} MB），請分成兩段再傳。",
        "en": "The recording is too large (over {limit_mb} MB). Please send it in two parts.",
        "id": "Rekaman terlalu besar (lebih dari {limit_mb} MB). Silakan kirim dalam dua bagian.",
        "vi": "Tệp ghi âm quá lớn (trên {limit_mb} MB). Vui lòng chia làm hai phần rồi gửi.",
        "th": "ไฟล์เสียงใหญ่เกินไป (เกิน {limit_mb} MB) กรุณาแบ่งเป็นสองส่วนแล้วส่งใหม่",
        "ja": "録音ファイルが大きすぎます（{limit_mb} MB 超）。2 つに分けて送ってください。",
    },
    "clinic.chat.download_failed": {
        "zh-TW": "錄音沒有收到，請再傳一次。",
        "en": "We didn't receive the recording. Please send it again.",
        "id": "Rekaman tidak diterima. Silakan kirim lagi.",
        "vi": "Chưa nhận được bản ghi. Vui lòng gửi lại.",
        "th": "ไม่ได้รับไฟล์เสียง กรุณาส่งอีกครั้ง",
        "ja": "録音を受け取れませんでした。もう一度送ってください。",
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
    # --- Flex：症狀對應建議科別 ---
    "flex.symptom.alt": {
        "zh-TW": "建議的看診方向",
        "en": "Suggested care departments",
        "id": "Saran poli untuk berobat",
        "vi": "Gợi ý chuyên khoa khám",
        "th": "แผนกที่แนะนำให้เข้ารับการตรวจ",
        "ja": "受診する診療科の目安",
    },
    "flex.symptom.header": {
        "zh-TW": "推薦掛號科別",
        "en": "Suggested Department",
        "id": "Poli yang Disarankan",
        "vi": "Chuyên Khoa Gợi Ý",
        "th": "แผนกที่แนะนำ",
        "ja": "おすすめの診療科",
    },
    "flex.symptom.tag.suggestion": {
        "zh-TW": "(建議優先)", "en": "(Start here)", "id": "(Prioritas)",
        "vi": "(Ưu tiên)", "th": "(แนะนำเป็นอันดับแรก)", "ja": "（優先候補）",
    },
    "flex.symptom.tag.fallback": {
        "zh-TW": "(不確定時的方向)", "en": "(When unsure)",
        "id": "(Jika belum yakin)", "vi": "(Khi chưa chắc chắn)",
        "th": "(เมื่อยังไม่แน่ใจ)", "ja": "（判断が難しい場合）",
    },
    "flex.symptom.body.suggestion": {
        "zh-TW": "依「{term}」整理的可能科別與評估原因",
        "en": "Possible departments based on the symptoms you described",
        "id": "Pilihan poli berdasarkan gejala yang Anda sampaikan",
        "vi": "Các chuyên khoa phù hợp dựa trên triệu chứng bạn mô tả",
        "th": "แผนกที่อาจเหมาะสมจากอาการที่คุณบอก",
        "ja": "お伝えいただいた症状から考えられる診療科",
    },
    "flex.symptom.body.fallback": {
        "zh-TW": "系統無法判斷你描述的狀況該掛哪一科（{reason}），以下是常見的初診方向",
        "en": "The system could not determine a specific department ({reason}). These are common places to start",
        "id": "Sistem belum dapat menentukan poli tertentu ({reason}). Berikut pilihan umum untuk kunjungan pertama",
        "vi": "Hệ thống chưa xác định được chuyên khoa cụ thể ({reason}). Bạn có thể bắt đầu với các khoa sau",
        "th": "ระบบยังระบุแผนกที่แน่ชัดไม่ได้ ({reason}) ต่อไปนี้คือแผนกทั่วไปสำหรับการตรวจครั้งแรก",
        "ja": "受診科を特定できませんでした（{reason}）。初診の一般的な候補はこちらです",
    },
    "flex.symptom.reason.label": {
        "zh-TW": "理由：{reason}", "en": "Why: {reason}",
        "id": "Alasan: {reason}", "vi": "Lý do: {reason}",
        "th": "เหตุผล: {reason}", "ja": "理由：{reason}",
    },
    "flex.symptom.reason.department": {
        "zh-TW": "{term}常見的看診方向之一是{department}。",
        "en": "{department} is one possible department for the symptoms you described.",
        "id": "{department} adalah salah satu poli yang mungkin sesuai untuk gejala Anda.",
        "vi": "{department} là một chuyên khoa có thể phù hợp với triệu chứng bạn mô tả.",
        "th": "{department} เป็นหนึ่งในแผนกที่อาจเหมาะกับอาการที่คุณบอก",
        "ja": "お伝えいただいた症状では、{department}が受診先の候補になります。",
    },
    "flex.symptom.reason.subgroup": {
        "zh-TW": "{term}在這類分科中通常由{department}的{subgroups}方向處理。",
        "en": "Within {department}, the relevant area is usually {subgroups}.",
        "id": "Di {department}, bidang yang biasanya terkait adalah {subgroups}.",
        "vi": "Trong {department}, lĩnh vực thường phù hợp là {subgroups}.",
        "th": "ภายใน{department} สาขาที่มักเกี่ยวข้องคือ{subgroups}",
        "ja": "{department}のうち、通常は{subgroups}の領域が対応します。",
    },
    "flex.symptom.alternative_separator": {
        "zh-TW": "或", "en": " or ", "id": " atau ",
        "vi": " hoặc ", "th": " หรือ ", "ja": "または",
    },
    "flex.symptom.source.label": {
        "zh-TW": "參考來源", "en": "References", "id": "Referensi",
        "vi": "Nguồn tham khảo", "th": "แหล่งอ้างอิง", "ja": "参考資料",
    },
    "flex.symptom.source.item": {
        "zh-TW": "{index}. {name}「該看哪一科」對照表",
        "en": "{index}. {name} department guide",
        "id": "{index}. Panduan poli dari {name}",
        "vi": "{index}. Hướng dẫn chọn chuyên khoa của {name}",
        "th": "{index}. คู่มือเลือกแผนกจาก {name}",
        "ja": "{index}. {name}の診療科案内",
    },
    "flex.symptom.source.open": {
        "zh-TW": "開啟參考網址{index}", "en": "Open reference {index}",
        "id": "Buka referensi {index}", "vi": "Mở nguồn {index}",
        "th": "เปิดแหล่งอ้างอิง {index}", "ja": "参考資料{index}を開く",
    },
    "flex.symptom.source.single": {
        "zh-TW": "（僅 1 家醫院的對照表收錄此症狀，建議先去電確認）",
        "en": " (Only one hospital guide lists this symptom; please call ahead to confirm.)",
        "id": " (Gejala ini hanya tercantum dalam panduan satu rumah sakit; sebaiknya telepon lebih dulu.)",
        "vi": " (Chỉ một bệnh viện liệt kê triệu chứng này; bạn nên gọi xác nhận trước.)",
        "th": " (มีคู่มือของโรงพยาบาลเพียงแห่งเดียวที่ระบุอาการนี้ แนะนำให้โทรยืนยันก่อน)",
        "ja": "（この症状を掲載している病院の案内は1件のみです。事前に電話でご確認ください）",
    },
    "flex.symptom.source.multiple": {
        "zh-TW": "（收錄此症狀的 {hospital_count} 家醫院中，有 {listed} 家列在此科{call_ahead}）",
        "en": " ({listed} of {hospital_count} hospital guides list this department{call_ahead})",
        "id": " ({listed} dari {hospital_count} panduan rumah sakit mencantumkan poli ini{call_ahead})",
        "vi": " ({listed} trong {hospital_count} bệnh viện liệt kê chuyên khoa này{call_ahead})",
        "th": " (คู่มือโรงพยาบาล {listed} จาก {hospital_count} แห่งระบุแผนกนี้{call_ahead})",
        "ja": "（{hospital_count}病院の案内のうち{listed}件がこの診療科を掲載{call_ahead}）",
    },
    "flex.symptom.source.call_ahead": {
        "zh-TW": "，建議先去電確認", "en": "; please call ahead to confirm",
        "id": "; sebaiknya telepon lebih dulu", "vi": "; bạn nên gọi xác nhận trước",
        "th": " แนะนำให้โทรยืนยันก่อน", "ja": "。事前の電話確認をおすすめします",
    },
    "flex.symptom.pediatric.mentioned": {
        "zh-TW": "因為是幫孩子詢問，另外列出兒科。",
        "en": "Because you are asking for a child, Pediatrics is also listed.",
        "id": "Karena pertanyaan ini untuk anak, Poli Anak juga dicantumkan.",
        "vi": "Vì bạn đang hỏi cho trẻ em, Nhi khoa cũng được liệt kê.",
        "th": "เนื่องจากสอบถามให้เด็ก จึงเพิ่มแผนกกุมารเวชกรรมไว้ด้วย",
        "ja": "お子さまについてのご相談のため、小児科も候補に含めています。",
    },
    "flex.symptom.pediatric.age": {
        "zh-TW": "因為你還未滿 {age} 歲，另外列出兒科。",
        "en": "Because you are under {age}, Pediatrics is also listed.",
        "id": "Karena usia Anda belum {age} tahun, Poli Anak juga dicantumkan.",
        "vi": "Vì bạn chưa đủ {age} tuổi, Nhi khoa cũng được liệt kê.",
        "th": "เนื่องจากคุณอายุต่ำกว่า {age} ปี จึงเพิ่มแผนกกุมารเวชกรรมไว้ด้วย",
        "ja": "{age}歳未満のため、小児科も候補に含めています。",
    },
    "flex.symptom.nearby.prompt": {
        "zh-TW": "是否需要搜尋附近{department}的醫院或診所？",
        "en": "Would you like to find nearby facilities with {department}?",
        "id": "Ingin mencari fasilitas {department} di sekitar Anda?",
        "vi": "Bạn có muốn tìm cơ sở {department} gần đây không?",
        "th": "ต้องการค้นหาสถานพยาบาล{department}ใกล้เคียงหรือไม่",
        "ja": "近くの{department}がある医療機関を検索しますか？",
    },
    "flex.symptom.nearby.fallback_prompt": {
        "zh-TW": "是否需要搜尋附近的醫院或診所？下方按鈕會一次搜尋{departments}。",
        "en": "Find nearby facilities? The button searches {departments} together.",
        "id": "Cari fasilitas terdekat? Tombol ini mencari {departments} sekaligus.",
        "vi": "Tìm cơ sở gần đây? Nút bên dưới sẽ tìm đồng thời {departments}.",
        "th": "ต้องการค้นหาสถานพยาบาลใกล้เคียงหรือไม่ ปุ่มด้านล่างจะค้นหา{departments}พร้อมกัน",
        "ja": "近くの医療機関を検索しますか？下のボタンで{departments}をまとめて検索します。",
    },
    "flex.symptom.nearby.button": {
        "zh-TW": "搜尋附近的{departments}", "en": "Find nearby {departments}",
        "id": "Cari {departments} terdekat", "vi": "Tìm {departments} gần đây",
        "th": "ค้นหา{departments}ใกล้เคียง", "ja": "近くの{departments}を検索",
    },
    "flex.symptom.disclaimer": {
        "zh-TW": "免責聲明：本建議僅供參考，不是醫療診斷。若症狀持續或惡化，請務必儘速就醫接受專業診斷。",
        "en": "Disclaimer: This guidance is for reference only and is not a medical diagnosis. Seek professional care promptly if symptoms persist or worsen.",
        "id": "Penafian: Saran ini hanya sebagai referensi dan bukan diagnosis medis. Segera cari pertolongan profesional jika gejala menetap atau memburuk.",
        "vi": "Lưu ý: Gợi ý này chỉ để tham khảo, không phải chẩn đoán y khoa. Hãy đi khám sớm nếu triệu chứng kéo dài hoặc nặng hơn.",
        "th": "ข้อสงวนสิทธิ์: คำแนะนำนี้ใช้เป็นข้อมูลอ้างอิงเท่านั้น ไม่ใช่การวินิจฉัย หากอาการไม่หายหรือรุนแรงขึ้น โปรดพบแพทย์โดยเร็ว",
        "ja": "免責事項：この案内は参考情報であり、医療診断ではありません。症状が続く、または悪化する場合は、早めに医療機関を受診してください。",
    },
    "flex.symptom.fallback_reason.unknown": {
        "zh-TW": "無法對應到已知的症狀條目", "en": "the symptom did not match a known entry",
        "id": "gejala tidak cocok dengan entri yang dikenal", "vi": "triệu chứng không khớp với mục đã biết",
        "th": "อาการไม่ตรงกับรายการที่ระบบรู้จัก", "ja": "既知の症状項目に一致しませんでした",
    },
    "flex.symptom.fallback_reason.broad": {
        "zh-TW": "這個症狀可能牽涉多個科別", "en": "the symptom may involve several departments",
        "id": "gejala mungkin melibatkan beberapa poli", "vi": "triệu chứng có thể liên quan đến nhiều chuyên khoa",
        "th": "อาการนี้อาจเกี่ยวข้องกับหลายแผนก", "ja": "複数の診療科に関係する可能性があります",
    },
    "flex.symptom.fallback_reason.pediatric_only": {
        "zh-TW": "這個症狀在對照表中只列了兒科", "en": "the guide lists this symptom only under Pediatrics",
        "id": "panduan hanya mencantumkan gejala ini di Poli Anak", "vi": "hướng dẫn chỉ liệt kê triệu chứng này ở Nhi khoa",
        "th": "คู่มือระบุอาการนี้ไว้เฉพาะกุมารเวชกรรม", "ja": "案内ではこの症状が小児科にのみ掲載されています",
    },
    "flex.symptom.fallback_reason.generic": {
        "zh-TW": "資訊不足", "en": "there was not enough information",
        "id": "informasi belum cukup", "vi": "chưa có đủ thông tin",
        "th": "ข้อมูลยังไม่เพียงพอ", "ja": "情報が不足しています",
    },
    "flex.symptom.plain.suggestion_header": {
        "zh-TW": "依「{term}」整理的看診方向：", "en": "Suggested departments based on your symptoms:",
        "id": "Poli yang disarankan berdasarkan gejala Anda:", "vi": "Chuyên khoa gợi ý dựa trên triệu chứng của bạn:",
        "th": "แผนกที่แนะนำจากอาการของคุณ:", "ja": "症状から考えられる診療科：",
    },
    "flex.symptom.plain.fallback_header": {
        "zh-TW": "系統無法判斷你描述的狀況該掛哪一科（{reason}）。",
        "en": "The system could not determine a specific department ({reason}).",
        "id": "Sistem belum dapat menentukan poli tertentu ({reason}).",
        "vi": "Hệ thống chưa xác định được chuyên khoa cụ thể ({reason}).",
        "th": "ระบบยังระบุแผนกที่แน่ชัดไม่ได้ ({reason})",
        "ja": "受診科を特定できませんでした（{reason}）。",
    },
    "flex.symptom.plain.suggestion_intro": {
        "zh-TW": "常見的看診方向：", "en": "Possible departments:",
        "id": "Pilihan poli:", "vi": "Các chuyên khoa có thể phù hợp:",
        "th": "แผนกที่อาจเหมาะสม:", "ja": "受診先の候補：",
    },
    "flex.symptom.plain.fallback_intro": {
        "zh-TW": "不確定時常見的初診方向：", "en": "Common places to start when unsure:",
        "id": "Pilihan umum untuk kunjungan pertama:", "vi": "Các khoa thường phù hợp cho lần khám đầu:",
        "th": "แผนกทั่วไปสำหรับการตรวจครั้งแรก:", "ja": "判断が難しい場合の一般的な初診先：",
    },
    "flex.symptom.plain.subgroup": {
        "zh-TW": "（{subgroups}方向）", "en": " ({subgroups})", "id": " ({subgroups})",
        "vi": " ({subgroups})", "th": " ({subgroups})", "ja": "（{subgroups}領域）",
    },
    "flex.symptom.unavailable": {
        "zh-TW": "科別建議服務未初始化，請稍後再試。",
        "en": "The department suggestion service is unavailable. Please try again later.",
        "id": "Layanan saran poli belum tersedia. Silakan coba lagi nanti.",
        "vi": "Dịch vụ gợi ý chuyên khoa hiện chưa khả dụng. Vui lòng thử lại sau.",
        "th": "บริการแนะนำแผนกยังไม่พร้อมใช้งาน โปรดลองอีกครั้งภายหลัง",
        "ja": "診療科案内サービスを利用できません。しばらくしてからもう一度お試しください。",
    },
    "flex.symptom.patient.ambiguous": {
        "zh-TW": "找到多位符合的家人：{names}。請說完整姓名後，再問一次要看哪一科。",
        "en": "More than one family member matches: {names}. Please give the full name, then ask again which department to visit.",
        "id": "Ada beberapa anggota keluarga yang cocok: {names}. Sebutkan nama lengkap, lalu tanyakan lagi poli yang sesuai.",
        "vi": "Có nhiều người thân phù hợp: {names}. Vui lòng cho biết họ tên đầy đủ, rồi hỏi lại nên khám chuyên khoa nào.",
        "th": "พบสมาชิกครอบครัวที่ตรงกันหลายคน: {names} โปรดบอกชื่อเต็ม แล้วถามอีกครั้งว่าควรไปแผนกใด",
        "ja": "該当するご家族が複数います：{names}。フルネームを伝えてから、受診科をもう一度お尋ねください。",
    },
    "flex.symptom.patient.conflict": {
        "zh-TW": "「{query}」與指定的稱謂不一致。請確認姓名或稱謂後，再問一次要看哪一科。",
        "en": "“{query}” does not match the specified relationship. Please check the name or relationship, then ask again which department to visit.",
        "id": "“{query}” tidak sesuai dengan hubungan yang disebutkan. Periksa nama atau hubungannya, lalu tanyakan lagi poli yang sesuai.",
        "vi": "“{query}” không khớp với quan hệ đã nêu. Hãy kiểm tra tên hoặc quan hệ, rồi hỏi lại nên khám chuyên khoa nào.",
        "th": "“{query}” ไม่ตรงกับความสัมพันธ์ที่ระบุ โปรดตรวจสอบชื่อหรือความสัมพันธ์ แล้วถามอีกครั้งว่าควรไปแผนกใด",
        "ja": "「{query}」は指定された続柄と一致しません。名前または続柄を確認してから、受診科をもう一度お尋ねください。",
    },
    "flex.symptom.sentence_separator": {
        "zh-TW": "。", "en": ". ", "id": ". ",
        "vi": ". ", "th": ". ", "ja": "。",
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
    # 症狀分科卡使用的次專科。這些是掛號時要指名的方向，不是資料庫搜尋條件。
    "subgroup.一般內科": {
        "zh-TW": "一般內科", "en": "General Internal Medicine", "id": "Penyakit Dalam Umum",
        "vi": "Nội tổng quát", "th": "อายุรกรรมทั่วไป", "ja": "一般内科",
    },
    "subgroup.一般外科": {
        "zh-TW": "一般外科", "en": "General Surgery", "id": "Bedah Umum",
        "vi": "Ngoại tổng quát", "th": "ศัลยกรรมทั่วไป", "ja": "一般外科",
    },
    "subgroup.大腸直腸外科": {
        "zh-TW": "大腸直腸外科", "en": "Colorectal Surgery", "id": "Bedah Kolorektal",
        "vi": "Ngoại đại trực tràng", "th": "ศัลยกรรมลำไส้ใหญ่และทวารหนัก", "ja": "大腸・直腸外科",
    },
    "subgroup.小兒外科": {
        "zh-TW": "小兒外科", "en": "Pediatric Surgery", "id": "Bedah Anak",
        "vi": "Ngoại nhi", "th": "ศัลยกรรมเด็ก", "ja": "小児外科",
    },
    "subgroup.心臟內科": {
        "zh-TW": "心臟內科", "en": "Cardiology", "id": "Kardiologi",
        "vi": "Tim mạch", "th": "อายุรศาสตร์โรคหัวใจ", "ja": "循環器内科",
    },
    "subgroup.心臟外科": {
        "zh-TW": "心臟外科", "en": "Cardiac Surgery", "id": "Bedah Jantung",
        "vi": "Phẫu thuật tim", "th": "ศัลยกรรมหัวใจ", "ja": "心臓外科",
    },
    "subgroup.生殖醫學科": {
        "zh-TW": "生殖醫學科", "en": "Reproductive Medicine", "id": "Kedokteran Reproduksi",
        "vi": "Y học sinh sản", "th": "เวชศาสตร์การเจริญพันธุ์", "ja": "生殖医療科",
    },
    "subgroup.安寧緩和科": {
        "zh-TW": "安寧緩和科", "en": "Palliative Care", "id": "Perawatan Paliatif",
        "vi": "Chăm sóc giảm nhẹ", "th": "การดูแลแบบประคับประคอง", "ja": "緩和ケア科",
    },
    "subgroup.血液科": {
        "zh-TW": "血液科", "en": "Hematology", "id": "Hematologi",
        "vi": "Huyết học", "th": "โลหิตวิทยา", "ja": "血液内科",
    },
    "subgroup.血液腫瘤科": {
        "zh-TW": "血液腫瘤科", "en": "Hematology-Oncology", "id": "Hematologi-Onkologi",
        "vi": "Huyết học ung bướu", "th": "โลหิตวิทยาและมะเร็งวิทยา", "ja": "血液腫瘍内科",
    },
    "subgroup.免疫風濕科": {
        "zh-TW": "免疫風濕科", "en": "Rheumatology and Immunology", "id": "Reumatologi dan Imunologi",
        "vi": "Miễn dịch và thấp khớp", "th": "ภูมิคุ้มกันและโรคข้อ", "ja": "リウマチ・免疫内科",
    },
    "subgroup.乳房外科": {
        "zh-TW": "乳房外科", "en": "Breast Surgery", "id": "Bedah Payudara",
        "vi": "Ngoại tuyến vú", "th": "ศัลยกรรมเต้านม", "ja": "乳腺外科",
    },
    "subgroup.兒童牙科": {
        "zh-TW": "兒童牙科", "en": "Pediatric Dentistry", "id": "Kedokteran Gigi Anak",
        "vi": "Nha khoa trẻ em", "th": "ทันตกรรมสำหรับเด็ก", "ja": "小児歯科",
    },
    "subgroup.兒童青少年精神科": {
        "zh-TW": "兒童青少年精神科", "en": "Child and Adolescent Psychiatry", "id": "Psikiatri Anak dan Remaja",
        "vi": "Tâm thần trẻ em và vị thành niên", "th": "จิตเวชเด็กและวัยรุ่น", "ja": "児童・思春期精神科",
    },
    "subgroup.泌尿科": {
        "zh-TW": "泌尿科", "en": "Urology", "id": "Urologi",
        "vi": "Tiết niệu", "th": "ระบบทางเดินปัสสาวะ", "ja": "泌尿器科",
    },
    "subgroup.胃腸肝膽科": {
        "zh-TW": "胃腸肝膽科", "en": "Gastroenterology and Hepatology", "id": "Gastroenterologi dan Hepatologi",
        "vi": "Tiêu hóa và gan mật", "th": "ระบบทางเดินอาหารและตับ", "ja": "消化器・肝臓内科",
    },
    "subgroup.消化外科": {
        "zh-TW": "消化外科", "en": "Gastrointestinal Surgery", "id": "Bedah Pencernaan",
        "vi": "Ngoại tiêu hóa", "th": "ศัลยกรรมทางเดินอาหาร", "ja": "消化器外科",
    },
    "subgroup.特殊需求者牙科": {
        "zh-TW": "特殊需求者牙科", "en": "Special Care Dentistry", "id": "Kedokteran Gigi Kebutuhan Khusus",
        "vi": "Nha khoa nhu cầu đặc biệt", "th": "ทันตกรรมสำหรับผู้มีความต้องการพิเศษ", "ja": "スペシャルニーズ歯科",
    },
    "subgroup.疼痛科": {
        "zh-TW": "疼痛科", "en": "Pain Medicine", "id": "Kedokteran Nyeri",
        "vi": "Điều trị đau", "th": "เวชศาสตร์ความปวด", "ja": "ペインクリニック",
    },
    "subgroup.神經內科": {
        "zh-TW": "神經內科", "en": "Neurology", "id": "Neurologi",
        "vi": "Thần kinh", "th": "ประสาทวิทยา", "ja": "神経内科",
    },
    "subgroup.胸腔內科": {
        "zh-TW": "胸腔內科", "en": "Pulmonology", "id": "Pulmonologi",
        "vi": "Hô hấp", "th": "อายุรศาสตร์โรคปอด", "ja": "呼吸器内科",
    },
    "subgroup.胸腔外科": {
        "zh-TW": "胸腔外科", "en": "Thoracic Surgery", "id": "Bedah Toraks",
        "vi": "Ngoại lồng ngực", "th": "ศัลยกรรมทรวงอก", "ja": "呼吸器外科",
    },
    "subgroup.高齡醫學科": {
        "zh-TW": "高齡醫學科", "en": "Geriatric Medicine", "id": "Geriatri",
        "vi": "Lão khoa", "th": "เวชศาสตร์ผู้สูงอายุ", "ja": "老年医学科",
    },
    "subgroup.婦科": {
        "zh-TW": "婦科", "en": "Gynecology", "id": "Ginekologi",
        "vi": "Phụ khoa", "th": "นรีเวช", "ja": "婦人科",
    },
    "subgroup.婦產科": {
        "zh-TW": "婦產科", "en": "Obstetrics and Gynecology", "id": "Obstetri dan Ginekologi",
        "vi": "Sản phụ khoa", "th": "สูติศาสตร์และนรีเวชวิทยา", "ja": "産婦人科",
    },
    "subgroup.產科": {
        "zh-TW": "產科", "en": "Obstetrics", "id": "Obstetri",
        "vi": "Sản khoa", "th": "สูติศาสตร์", "ja": "産科",
    },
    "subgroup.腎臟內科": {
        "zh-TW": "腎臟內科", "en": "Nephrology", "id": "Nefrologi",
        "vi": "Thận học", "th": "อายุรศาสตร์โรคไต", "ja": "腎臓内科",
    },
    "subgroup.感染科": {
        "zh-TW": "感染科", "en": "Infectious Diseases", "id": "Penyakit Infeksi",
        "vi": "Bệnh truyền nhiễm", "th": "โรคติดเชื้อ", "ja": "感染症内科",
    },
    "subgroup.新陳代謝及內分泌科": {
        "zh-TW": "新陳代謝及內分泌科", "en": "Endocrinology and Metabolism", "id": "Endokrinologi dan Metabolisme",
        "vi": "Nội tiết và chuyển hóa", "th": "ต่อมไร้ท่อและเมแทบอลิซึม", "ja": "内分泌・代謝内科",
    },
    "subgroup.腫瘤內科": {
        "zh-TW": "腫瘤內科", "en": "Medical Oncology", "id": "Onkologi Medik",
        "vi": "Ung bướu nội khoa", "th": "มะเร็งวิทยาอายุรกรรม", "ja": "腫瘍内科",
    },
    "subgroup.臨床毒物科": {
        "zh-TW": "臨床毒物科", "en": "Clinical Toxicology", "id": "Toksikologi Klinis",
        "vi": "Độc chất học lâm sàng", "th": "พิษวิทยาคลินิก", "ja": "臨床中毒科",
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
    # --- 家庭名單與稱謂查詢 ---
    "family.directory.relationship.parent": {
        "zh-TW": "父／母", "en": "parent", "id": "orang tua",
        "vi": "cha/mẹ", "th": "พ่อ/แม่", "ja": "親",
    },
    "family.directory.relationship.child": {
        "zh-TW": "子／女", "en": "child", "id": "anak",
        "vi": "con", "th": "ลูก", "ja": "子",
    },
    "family.directory.relationship.spouse": {
        "zh-TW": "配偶", "en": "spouse", "id": "pasangan",
        "vi": "vợ/chồng", "th": "คู่สมรส", "ja": "配偶者",
    },
    "family.directory.relationship.sibling": {
        "zh-TW": "兄弟姊妹", "en": "sibling", "id": "saudara kandung",
        "vi": "anh chị em", "th": "พี่น้อง", "ja": "兄弟姉妹",
    },
    "family.directory.relationship.grandparent": {
        "zh-TW": "祖父母", "en": "grandparent", "id": "kakek/nenek",
        "vi": "ông/bà", "th": "ปู่ย่าตายาย", "ja": "祖父母",
    },
    "family.directory.relationship.grandchild": {
        "zh-TW": "孫子女", "en": "grandchild", "id": "cucu",
        "vi": "cháu", "th": "หลาน", "ja": "孫",
    },
    "family.directory.relationship.other": {
        "zh-TW": "其他", "en": "other", "id": "lainnya",
        "vi": "khác", "th": "อื่น ๆ", "ja": "その他",
    },
    "family.directory.relationship.unset": {
        "zh-TW": "尚未設定稱謂", "en": "relationship not set",
        "id": "hubungan belum diatur", "vi": "chưa đặt quan hệ",
        "th": "ยังไม่ได้ตั้งความสัมพันธ์", "ja": "続柄未設定",
    },
    "family.directory.error": {
        "zh-TW": "暫時查不到家庭名單，請稍後再試。",
        "en": "I can't look up your family list right now. Please try again later.",
        "id": "Daftar keluarga belum dapat dilihat. Silakan coba lagi nanti.",
        "vi": "Hiện chưa tra được danh sách gia đình. Vui lòng thử lại sau.",
        "th": "ตอนนี้ยังดูรายชื่อครอบครัวไม่ได้ กรุณาลองใหม่ภายหลัง",
        "ja": "現在ご家族の一覧を確認できません。しばらくしてからもう一度お試しください。",
    },
    "family.directory.empty": {
        "zh-TW": "您的家庭名單目前是空的。",
        "en": "Your family list is currently empty.",
        "id": "Daftar keluarga Anda masih kosong.",
        "vi": "Danh sách gia đình của bạn hiện đang trống.",
        "th": "รายชื่อครอบครัวของคุณยังว่างอยู่",
        "ja": "ご家族の一覧は現在空です。",
    },
    "family.directory.unsupported_relationship": {
        "zh-TW": "這個稱謂目前不在可查詢的家庭關係中。",
        "en": "That relationship isn't available in the family list.",
        "id": "Hubungan tersebut tidak tersedia dalam daftar keluarga.",
        "vi": "Quan hệ đó chưa có trong danh sách gia đình.",
        "th": "ยังไม่มีความสัมพันธ์นี้ในรายชื่อครอบครัว",
        "ja": "その続柄はご家族の一覧で利用できません。",
    },
    "family.directory.person": {
        "zh-TW": "您將{name}設定為{relationship}。",
        "en": "You have {name} listed as your {relationship}.",
        "id": "Anda mencatat {name} sebagai {relationship} Anda.",
        "vi": "Bạn đã đặt {name} là {relationship} của mình.",
        "th": "คุณตั้ง {name} เป็น{relationship}ของคุณ",
        "ja": "{name}さんはあなたの{relationship}として設定されています。",
    },
    "family.directory.person_unset": {
        "zh-TW": "{name}在您的家庭名單中，但尚未設定稱謂。",
        "en": "{name} is in your family list, but their relationship is not set.",
        "id": "{name} ada di daftar keluarga Anda, tetapi hubungannya belum diatur.",
        "vi": "{name} có trong danh sách gia đình, nhưng chưa đặt quan hệ.",
        "th": "{name} อยู่ในรายชื่อครอบครัว แต่ยังไม่ได้ตั้งความสัมพันธ์",
        "ja": "{name}さんはご家族の一覧にいますが、続柄は未設定です。",
    },
    "family.directory.self": {
        "zh-TW": "{name}就是您本人。",
        "en": "{name} is you.",
        "id": "{name} adalah Anda sendiri.",
        "vi": "{name} chính là bạn.",
        "th": "{name} คือคุณเอง",
        "ja": "{name}さんはあなたご本人です。",
    },
    "family.directory.self_unnamed": {
        "zh-TW": "這是您本人，不是家庭名單中的另一位成員。",
        "en": "That's you, not another member of your family list.",
        "id": "Itu adalah Anda sendiri, bukan anggota lain dalam daftar keluarga.",
        "vi": "Đó là chính bạn, không phải một thành viên khác trong danh sách gia đình.",
        "th": "นั่นคือคุณเอง ไม่ใช่สมาชิกคนอื่นในรายชื่อครอบครัว",
        "ja": "それはご本人で、ご家族の一覧にいる別の方ではありません。",
    },
    "family.directory.self_ambiguous": {
        "zh-TW": "「{name}」同時符合您本人與家庭名單中的成員。請改用稱謂或其他可辨識方式。",
        "en": "“{name}” matches both you and a member of your family list. Please use a relationship or another distinguishing detail.",
        "id": "“{name}” cocok dengan Anda dan anggota dalam daftar keluarga. Gunakan hubungan atau keterangan pembeda lain.",
        "vi": "“{name}” khớp với cả bạn và một thành viên trong danh sách gia đình. Hãy dùng quan hệ hoặc thông tin phân biệt khác.",
        "th": "“{name}” ตรงกับทั้งคุณและสมาชิกในรายชื่อครอบครัว โปรดใช้ความสัมพันธ์หรือข้อมูลอื่นเพื่อแยกบุคคล",
        "ja": "「{name}」はご本人とご家族の一覧のメンバーの両方に一致します。続柄など別の識別情報をお使いください。",
    },
    "family.directory.ambiguous": {
        "zh-TW": "找到多位名稱相近的家人：{names}。請說完整姓名。",
        "en": "More than one family member has a similar name: {names}. Please give the full name.",
        "id": "Ada beberapa nama keluarga yang mirip: {names}. Sebutkan nama lengkap.",
        "vi": "Có nhiều người thân có tên gần giống: {names}. Vui lòng nói đầy đủ họ tên.",
        "th": "พบชื่อคนในครอบครัวที่คล้ายกันหลายคน: {names} โปรดบอกชื่อเต็ม",
        "ja": "似たお名前のご家族が複数います：{names}。フルネームを教えてください。",
    },
    "family.directory.conflict": {
        "zh-TW": "「{query}」與指定的稱謂不一致，請確認姓名或稱謂。",
        "en": "“{query}” doesn't match the specified relationship. Please check the name or relationship.",
        "id": "“{query}” tidak sesuai dengan hubungan yang disebutkan. Periksa nama atau hubungannya.",
        "vi": "“{query}” không khớp với quan hệ đã nêu. Hãy kiểm tra tên hoặc quan hệ.",
        "th": "“{query}” ไม่ตรงกับความสัมพันธ์ที่ระบุ โปรดตรวจสอบชื่อหรือความสัมพันธ์",
        "ja": "「{query}」は指定された続柄と一致しません。名前または続柄をご確認ください。",
    },
    "family.directory.not_found": {
        "zh-TW": "您的家庭名單中找不到「{query}」。",
        "en": "I couldn't find “{query}” in your family list.",
        "id": "“{query}” tidak ditemukan di daftar keluarga Anda.",
        "vi": "Không tìm thấy “{query}” trong danh sách gia đình của bạn.",
        "th": "ไม่พบ “{query}” ในรายชื่อครอบครัวของคุณ",
        "ja": "ご家族の一覧に「{query}」が見つかりません。",
    },
    "family.directory.no_relationship": {
        "zh-TW": "您的家庭名單中，沒有設定為{relationship}的成員。",
        "en": "No one in your family list is set as your {relationship}.",
        "id": "Tidak ada anggota yang ditetapkan sebagai {relationship} Anda.",
        "vi": "Không có ai được đặt là {relationship} của bạn.",
        "th": "ไม่มีใครในรายชื่อที่ตั้งเป็น{relationship}ของคุณ",
        "ja": "ご家族の一覧に{relationship}として設定された方はいません。",
    },
    "family.directory.relationship": {
        "zh-TW": "您設定為{relationship}的家人：{names}。",
        "en": "Listed as your {relationship}: {names}.",
        "id": "Tercatat sebagai {relationship} Anda: {names}.",
        "vi": "Được đặt là {relationship} của bạn: {names}.",
        "th": "คนที่ตั้งเป็น{relationship}ของคุณ: {names}",
        "ja": "{relationship}として設定されているご家族：{names}。",
    },
    "family.directory.list_header": {
        "zh-TW": "您的家庭名單：", "en": "Your family list:",
        "id": "Daftar keluarga Anda:", "vi": "Danh sách gia đình của bạn:",
        "th": "รายชื่อครอบครัวของคุณ:", "ja": "ご家族の一覧：",
    },
    "family.directory.list_item": {
        "zh-TW": "{name}：{relationship}", "en": "{name}: {relationship}",
        "id": "{name}: {relationship}", "vi": "{name}: {relationship}",
        "th": "{name}: {relationship}", "ja": "{name}：{relationship}",
    },
    "family.directory.list_sep": {
        "zh-TW": "、", "en": ", ", "id": ", ", "vi": ", ", "th": ", ", "ja": "、",
    },
    "family.directory.unnamed": {
        "zh-TW": "未設定名字的家人", "en": "an unnamed family member",
        "id": "anggota keluarga tanpa nama", "vi": "người thân chưa có tên",
        "th": "คนในครอบครัวที่ยังไม่มีชื่อ", "ja": "名前未設定のご家族",
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
    "medstatus.conflict": {
        "zh-TW": "「{query}」與您指定的親屬關係不一致。請確認姓名或關係後再問一次。",
        "en": "“{query}” doesn't match the family relationship you specified. Please check the name or relationship and ask again.",
        "id": "“{query}” tidak sesuai dengan hubungan keluarga yang Anda sebutkan. Periksa nama atau hubungannya, lalu tanyakan lagi.",
        "vi": "“{query}” không khớp với quan hệ gia đình bạn đã nêu. Hãy kiểm tra tên hoặc quan hệ rồi hỏi lại.",
        "th": "“{query}” ไม่ตรงกับความสัมพันธ์ในครอบครัวที่ระบุ โปรดตรวจสอบชื่อหรือความสัมพันธ์แล้วถามอีกครั้ง",
        "ja": "「{query}」は指定された家族関係と一致しません。名前または関係を確認して、もう一度お尋ねください。",
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
    # --- 拿自己的藥單問問題（MedicationQuestionService）---
    # 時間那幾句是程式從資料庫算出來的事實，只陳述數字、不下判斷——隔多久算
    # 安全是醫囑，不是我們算得出來的。
    "medq.facts_self": {
        "zh-TW": "以下是 CARE 裡登記的資料：\n{body}",
        "en": "From your CARE records:\n{body}",
        "id": "Dari catatan CARE Anda:\n{body}",
        "vi": "Theo dữ liệu đã lưu trong CARE:\n{body}",
        "th": "จากข้อมูลที่บันทึกไว้ใน CARE:\n{body}",
        "ja": "CARE に登録されている情報：\n{body}",
    },
    "medq.facts_other": {
        "zh-TW": "以下是 CARE 裡登記的{name}的資料：\n{body}",
        "en": "From {name}'s CARE records:\n{body}",
        "id": "Dari catatan CARE {name}:\n{body}",
        "vi": "Theo dữ liệu của {name} trong CARE:\n{body}",
        "th": "จากข้อมูลของ {name} ใน CARE:\n{body}",
        "ja": "CARE に登録されている{name}さんの情報：\n{body}",
    },
    "medq.meds": {
        "zh-TW": "目前登記 {n} 種藥：{names}",
        "en": "{n} medicines on record: {names}",
        "id": "{n} obat tercatat: {names}",
        "vi": "có {n} thuốc được ghi nhận: {names}",
        "th": "มียาที่บันทึกไว้ {n} รายการ: {names}",
        "ja": "登録されているお薬は {n} 種類：{names}",
    },
    "medq.slots_today": {
        "zh-TW": "今天排定{slots}",
        "en": "today's times are {slots}",
        "id": "jadwal hari ini {slots}",
        "vi": "hôm nay theo lịch {slots}",
        "th": "วันนี้ตามกำหนด {slots}",
        "ja": "今日の予定は{slots}",
    },
    "medq.late": {
        "zh-TW": "{slot} 那一頓在 {actual} 才確認，比排定時間晚 {delay}",
        "en": "the {slot} dose was confirmed at {actual}, {delay} later than scheduled",
        "id": "dosis {slot} baru dikonfirmasi pukul {actual}, {delay} lebih lambat dari jadwal",
        "vi": "liều {slot} đến {actual} mới xác nhận, muộn hơn lịch {delay}",
        "th": "ยามื้อ {slot} ยืนยันตอน {actual} ช้ากว่ากำหนด {delay}",
        "ja": "{slot} の分は {actual} に確認され、予定より {delay} 遅い",
    },
    "medq.gap": {
        "zh-TW": "{actual} 到下一頓{next_slot} 只隔 {actual_gap}，排定的間隔是 {planned_gap}",
        "en": (
            "from {actual} to the next dose at {next_slot} is {actual_gap}, "
            "while the scheduled interval is {planned_gap}"
        ),
        "id": (
            "dari {actual} ke dosis berikutnya {next_slot} hanya {actual_gap}, "
            "sedangkan jarak terjadwal {planned_gap}"
        ),
        "vi": (
            "từ {actual} đến liều kế tiếp {next_slot} chỉ cách {actual_gap}, "
            "trong khi lịch đặt là {planned_gap}"
        ),
        "th": (
            "จาก {actual} ถึงมื้อถัดไป {next_slot} ห่างกันเพียง {actual_gap} "
            "ขณะที่ตามกำหนดห่าง {planned_gap}"
        ),
        "ja": (
            "{actual} から次の {next_slot} までは {actual_gap}、"
            "予定の間隔は {planned_gap}"
        ),
    },
    "medq.duration_h": {
        "zh-TW": "{h} 小時", "en": "{h} hours", "id": "{h} jam",
        "vi": "{h} giờ", "th": "{h} ชั่วโมง", "ja": "{h} 時間",
    },
    "medq.duration_hm": {
        "zh-TW": "{h} 小時 {m} 分", "en": "{h} hours {m} minutes",
        "id": "{h} jam {m} menit", "vi": "{h} giờ {m} phút",
        "th": "{h} ชั่วโมง {m} นาที", "ja": "{h} 時間 {m} 分",
    },
    "medq.ask_pharmacist": {
        "zh-TW": "服藥時間或劑量要不要調整，請先問藥師或醫師",
        "en": "ask a pharmacist or doctor before changing any dose or timing",
        "id": "tanyakan ke apoteker atau dokter sebelum mengubah dosis atau waktu minum",
        "vi": "hãy hỏi dược sĩ hoặc bác sĩ trước khi đổi liều hay giờ uống",
        "th": "ควรถามเภสัชกรหรือแพทย์ก่อนปรับขนาดยาหรือเวลากินยา",
        "ja": "服用時間や量を変える前に、薬剤師か医師に相談してください",
    },
    "medq.no_answer": {
        "zh-TW": "這個問題我查不到可靠的資料，請直接問藥師或醫師；藥師看得到完整處方，判斷會比較準。",
        "en": (
            "I could not find reliable information for this question. "
            "Please ask a pharmacist or doctor, who can see the full prescription."
        ),
        "id": (
            "Saya tidak menemukan informasi yang dapat diandalkan untuk pertanyaan ini. "
            "Silakan tanya apoteker atau dokter yang bisa melihat resep lengkap."
        ),
        "vi": (
            "Tôi không tìm được thông tin đáng tin cậy cho câu hỏi này. "
            "Hãy hỏi dược sĩ hoặc bác sĩ, họ xem được toàn bộ đơn thuốc."
        ),
        "th": (
            "ฉันหาข้อมูลที่เชื่อถือได้สำหรับคำถามนี้ไม่พบ "
            "กรุณาถามเภสัชกรหรือแพทย์ซึ่งเห็นใบสั่งยาทั้งหมด"
        ),
        "ja": (
            "この質問に確かな情報が見つかりませんでした。"
            "処方の全体を見られる薬剤師か医師に直接ご相談ください。"
        ),
    },
    # --- 在聊天裡回報吃過藥了（MedicationReportService）---
    "medreport.done": {
        "zh-TW": "好，已記錄您在 {time} 服用{slot} 這一頓：",
        "en": "Done. Recorded that you took the {slot} dose at {time}:",
        "id": "Sudah dicatat: Anda minum dosis {slot} pukul {time}:",
        "vi": "Đã ghi nhận bạn uống liều {slot} lúc {time}:",
        "th": "บันทึกแล้วว่าคุณกินยามื้อ {slot} ตอน {time}:",
        "ja": "{time} に{slot}の分を服用したと記録しました：",
    },
    "medreport.which_slot": {
        "zh-TW": "請問是哪一頓？今天還沒確認的有：{slots}",
        "en": "Which dose was it? Still unconfirmed today: {slots}",
        "id": "Dosis yang mana? Yang belum dikonfirmasi hari ini: {slots}",
        "vi": "Là liều nào vậy? Hôm nay chưa xác nhận: {slots}",
        "th": "เป็นยามื้อไหนคะ วันนี้ที่ยังไม่ยืนยัน: {slots}",
        "ja": "どの分でしょうか。今日まだ未確認なのは：{slots}",
    },
    "medreport.nothing_open": {
        "zh-TW": "今天沒有待確認的時段，可能已經都確認過了。",
        "en": "There is no dose waiting for confirmation today; they may all be confirmed already.",
        "id": "Tidak ada dosis yang menunggu konfirmasi hari ini; mungkin semua sudah dikonfirmasi.",
        "vi": "Hôm nay không còn liều nào chờ xác nhận, có thể đã xác nhận hết rồi.",
        "th": "วันนี้ไม่มีมื้อที่รอการยืนยัน อาจยืนยันครบแล้ว",
        "ja": "今日は確認待ちの分がありません。すでにすべて確認済みかもしれません。",
    },
    "medreport.slot_not_open": {
        "zh-TW": "{slot}那一頓今天沒有待確認的紀錄，可能已經確認過、或今天沒有排。",
        "en": (
            "There is no unconfirmed {slot} dose today — it may already be confirmed, "
            "or not scheduled today."
        ),
        "id": (
            "Tidak ada dosis {slot} yang belum dikonfirmasi hari ini — mungkin sudah "
            "dikonfirmasi atau memang tidak dijadwalkan."
        ),
        "vi": (
            "Hôm nay không có liều {slot} nào chưa xác nhận — có thể đã xác nhận "
            "hoặc hôm nay không có lịch."
        ),
        "th": "วันนี้ไม่มียามื้อ{slot}ที่รอการยืนยัน อาจยืนยันแล้วหรือไม่ได้ตั้งไว้",
        "ja": "今日は{slot}の未確認の分がありません。確認済みか、今日は予定がないようです。",
    },
    "medreport.self_only": {
        "zh-TW": "服藥確認只能由本人回報，請家人自己在 CARE 裡按下確認。",
        "en": (
            "Only the person taking the medicine can confirm a dose. "
            "Please ask them to confirm it in CARE themselves."
        ),
        "id": (
            "Konfirmasi minum obat hanya bisa dilakukan oleh yang bersangkutan. "
            "Mintalah dia mengonfirmasi sendiri di CARE."
        ),
        "vi": (
            "Chỉ người uống thuốc mới xác nhận được. "
            "Hãy nhờ người đó tự xác nhận trong CARE."
        ),
        "th": "การยืนยันการกินยาต้องทำโดยเจ้าตัวเท่านั้น กรุณาให้เขายืนยันใน CARE เอง",
        "ja": "服薬の確認はご本人だけができます。ご本人に CARE で確認してもらってください。",
    },
    "medreport.error": {
        "zh-TW": "這次沒有記錄成功，請直接在用藥提醒訊息上按下確認。",
        "en": "That did not get recorded. Please confirm on the medication reminder message instead.",
        "id": "Belum tercatat. Silakan konfirmasi lewat pesan pengingat obat.",
        "vi": "Lần này chưa ghi nhận được. Bạn hãy xác nhận trên tin nhắn nhắc uống thuốc nhé.",
        "th": "ครั้งนี้บันทึกไม่สำเร็จ กรุณากดยืนยันที่ข้อความเตือนกินยาแทน",
        "ja": "今回は記録できませんでした。お薬のリマインダーから確認してください。",
    },
    # --- Flex：聊天裡回報服藥（medication_report_flex）---
    "flex.medreport.header.done": {
        "zh-TW": "已記錄服藥", "en": "Dose recorded", "id": "Obat tercatat",
        "vi": "Đã ghi nhận", "th": "บันทึกการกินยาแล้ว", "ja": "服薬を記録しました",
    },
    "flex.medreport.header.reverted": {
        "zh-TW": "已取消這筆記錄", "en": "Record cancelled", "id": "Catatan dibatalkan",
        "vi": "Đã huỷ ghi nhận", "th": "ยกเลิกการบันทึกแล้ว", "ja": "記録を取り消しました",
    },
    "flex.medreport.header.which": {
        "zh-TW": "請問是哪一頓？", "en": "Which dose?", "id": "Dosis yang mana?",
        "vi": "Là liều nào?", "th": "ยามื้อไหน?", "ja": "どの分でしょうか",
    },
    "flex.medreport.taken_at": {
        "zh-TW": "{time} 服用", "en": "Taken at {time}", "id": "Diminum pukul {time}",
        "vi": "Uống lúc {time}", "th": "กินตอน {time}", "ja": "{time} に服用",
    },
    "flex.medreport.back_to_unconfirmed": {
        "zh-TW": "已改回未確認",
        "en": "Back to unconfirmed",
        "id": "Kembali ke belum dikonfirmasi",
        "vi": "Trở lại trạng thái chưa xác nhận",
        "th": "กลับเป็นยังไม่ยืนยัน",
        "ja": "未確認に戻しました",
    },
    "flex.medreport.hint.done": {
        "zh-TW": "記錯了可以按下面取消。",
        "en": "If this is wrong, cancel it below.",
        "id": "Jika salah, batalkan di bawah.",
        "vi": "Nếu sai, hãy huỷ ở bên dưới.",
        "th": "ถ้าไม่ถูกต้อง กดยกเลิกด้านล่างได้",
        "ja": "間違いなら下から取り消せます。",
    },
    "flex.medreport.hint.reverted": {
        "zh-TW": "這一頓回到未確認，用藥提醒會照常提醒您。",
        "en": "This dose is unconfirmed again; reminders will continue as usual.",
        "id": "Dosis ini kembali belum dikonfirmasi; pengingat akan berjalan seperti biasa.",
        "vi": "Liều này trở lại chưa xác nhận, nhắc nhở sẽ tiếp tục như thường.",
        "th": "ยามื้อนี้กลับเป็นยังไม่ยืนยัน ระบบจะเตือนตามปกติ",
        "ja": "この分は未確認に戻り、リマインダーは通常どおり届きます。",
    },
    "flex.medreport.button.undo": {
        "zh-TW": "記錯了，取消這筆", "en": "That is wrong, cancel it",
        "id": "Salah, batalkan", "vi": "Ghi sai, huỷ đi",
        "th": "บันทึกผิด ยกเลิก", "ja": "間違いなので取り消す",
    },
    "flex.medreport.display.undo": {
        "zh-TW": "記錯了，取消這筆", "en": "That is wrong, cancel it",
        "id": "Salah, batalkan", "vi": "Ghi sai, huỷ đi",
        "th": "บันทึกผิด ยกเลิก", "ja": "間違いなので取り消す",
    },
    "flex.medreport.display.slot": {
        "zh-TW": "我吃的是{slot}這一頓", "en": "It was the {slot} dose",
        "id": "Itu dosis {slot}", "vi": "Đó là liều {slot}",
        "th": "เป็นยามื้อ {slot}", "ja": "{slot} の分です",
    },
    "flex.medreport.which_slot": {
        "zh-TW": "今天還沒確認的有這幾頓，請按您剛才吃的那一頓。",
        "en": "These doses are still unconfirmed today. Tap the one you took.",
        "id": "Dosis berikut belum dikonfirmasi hari ini. Ketuk yang Anda minum.",
        "vi": "Hôm nay còn những liều sau chưa xác nhận. Hãy chọn liều bạn đã uống.",
        "th": "วันนี้ยังไม่ยืนยันมื้อเหล่านี้ กดเลือกมื้อที่คุณกินไป",
        "ja": "今日まだ未確認の分です。飲んだものをタップしてください。",
    },
    "flex.medreport.alt.done": {
        "zh-TW": "已記錄{slot}的服藥", "en": "Recorded the {slot} dose",
        "id": "Dosis {slot} tercatat", "vi": "Đã ghi nhận liều {slot}",
        "th": "บันทึกยามื้อ {slot} แล้ว", "ja": "{slot} の服薬を記録しました",
    },
    "flex.medreport.alt.reverted": {
        "zh-TW": "已取消{slot}的服藥記錄", "en": "Cancelled the {slot} record",
        "id": "Catatan dosis {slot} dibatalkan", "vi": "Đã huỷ ghi nhận liều {slot}",
        "th": "ยกเลิกบันทึกยามื้อ {slot} แล้ว", "ja": "{slot} の記録を取り消しました",
    },
    "medreport.undo_failed": {
        "zh-TW": "這筆已經沒辦法取消了，請在用藥提醒訊息上重新確認。",
        "en": "This record can no longer be cancelled. Please confirm again on the reminder message.",
        "id": "Catatan ini tidak bisa dibatalkan lagi. Silakan konfirmasi ulang lewat pesan pengingat.",
        "vi": "Ghi nhận này không huỷ được nữa. Bạn hãy xác nhận lại trên tin nhắn nhắc uống thuốc.",
        "th": "รายการนี้ยกเลิกไม่ได้แล้ว กรุณายืนยันใหม่ที่ข้อความเตือนกินยา",
        "ja": "この記録はもう取り消せません。リマインダーから再度確認してください。",
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
    # 用藥提醒拉霸的語氣版本（見 app/services/medication/reminder_variants.py）。
    # 現行版就是上面的 flex.med.instruction；這兩版只換說法、不換要做的事，都是
    # 吃完藥按按鈕。家人版只給家屬設定的提醒——「家人就知道」只有那時才成立。
    "flex.med.instruction.family": {
        "zh-TW": "吃完藥按一下下面的按鈕，家人就知道你吃過了。",
        "en": "After taking your medicine, tap the button below so your family knows you've taken it.",
        "id": "Setelah minum obat, ketuk tombol di bawah supaya keluarga tahu Anda sudah minum.",
        "vi": "Uống thuốc xong, nhấn nút bên dưới để gia đình biết bạn đã uống.",
        "th": "ทานยาเสร็จแล้วแตะปุ่มด้านล่าง ครอบครัวจะได้รู้ว่าคุณทานแล้ว",
        "ja": "薬を飲んだら下のボタンを押してください。ご家族に飲んだことが伝わります。",
    },
    "flex.med.instruction.brief": {
        "zh-TW": "吃藥時間到了，吃完按下面的按鈕。",
        "en": "Time for your medicine. Tap the button below when you're done.",
        "id": "Waktunya minum obat. Setelah minum, ketuk tombol di bawah.",
        "vi": "Đến giờ uống thuốc rồi. Uống xong nhấn nút bên dưới.",
        "th": "ถึงเวลาทานยาแล้ว ทานเสร็จแตะปุ่มด้านล่าง",
        "ja": "お薬の時間です。飲んだら下のボタンを押してください。",
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
    # T+20 催促卡的語氣版本，與 flex.med.instruction.family／.brief 成對：
    # 同一頓藥的 T+0 與 T+20 用同一種語氣。
    "flex.med.urgent_body.family": {
        "zh-TW": "還沒收到你的確認。吃完按一下，家人就不用擔心。",
        "en": "We haven't received your confirmation yet. Tap the button after taking your medicine so your family won't worry.",
        "id": "Kami belum menerima konfirmasi Anda. Ketuk tombol setelah minum obat supaya keluarga tidak khawatir.",
        "vi": "Chúng tôi chưa nhận được xác nhận của bạn. Uống xong nhấn nút để gia đình khỏi lo.",
        "th": "ยังไม่ได้รับการยืนยันจากคุณ ทานยาเสร็จแตะปุ่ม ครอบครัวจะได้ไม่ต้องกังวล",
        "ja": "まだ確認が届いていません。飲んだらボタンを押してください。ご家族が安心します。",
    },
    "flex.med.urgent_body.brief": {
        "zh-TW": "還沒按喔，吃完藥記得按下面的按鈕。",
        "en": "You haven't tapped yet. Remember to tap the button below after taking your medicine.",
        "id": "Belum diketuk nih. Setelah minum obat, jangan lupa ketuk tombol di bawah.",
        "vi": "Bạn chưa nhấn nút. Uống thuốc xong nhớ nhấn nút bên dưới nhé.",
        "th": "ยังไม่ได้แตะเลยนะ ทานยาเสร็จอย่าลืมแตะปุ่มด้านล่าง",
        "ja": "まだ押されていません。飲んだら下のボタンを押すのを忘れずに。",
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
    # 家屬逾時警報的標題。收件人是家庭授權通知政策（medication_missed）選出的
    # 家屬，依授權本來就看得到用藥設定；讓警報講清楚是哪幾種藥沒吃，家屬才知道
    # 這次漏掉的嚴重程度，不必再回頭翻 LIFF 才能判斷。
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
    # --- 血壓／血糖超出範圍推播（health-alerts spec「超出範圍推播的內容」）---
    #
    # 內容故意樸素：數值、量測時間、被超過的範圍值、代記者，沒有診斷、沒有
    # 治療建議、沒有 119 區塊（design.md 決策 10）——這是一則「請留意、去看
    # 紀錄」的提醒，不是安全通報。
    "flex.health_alert.header.bp_high": {
        "zh-TW": "血壓偏高提醒",
        "en": "Blood pressure alert: high",
        "id": "Peringatan tekanan darah tinggi",
        "vi": "Cảnh báo huyết áp cao",
        "th": "แจ้งเตือนความดันโลหิตสูง",
        "ja": "血圧上昇のお知らせ",
    },
    "flex.health_alert.header.bp_low": {
        "zh-TW": "血壓偏低提醒",
        "en": "Blood pressure alert: low",
        "id": "Peringatan tekanan darah rendah",
        "vi": "Cảnh báo huyết áp thấp",
        "th": "แจ้งเตือนความดันโลหิตต่ำ",
        "ja": "血圧低下のお知らせ",
    },
    "flex.health_alert.header.glucose_high": {
        "zh-TW": "血糖偏高提醒",
        "en": "Blood glucose alert: high",
        "id": "Peringatan gula darah tinggi",
        "vi": "Cảnh báo đường huyết cao",
        "th": "แจ้งเตือนน้ำตาลในเลือดสูง",
        "ja": "血糖上昇のお知らせ",
    },
    "flex.health_alert.header.glucose_low": {
        "zh-TW": "血糖偏低提醒",
        "en": "Blood glucose alert: low",
        "id": "Peringatan gula darah rendah",
        "vi": "Cảnh báo đường huyết thấp",
        "th": "แจ้งเตือนน้ำตาลในเลือดต่ำ",
        "ja": "血糖低下のお知らせ",
    },
    "flex.health_alert.alt.bp_high": {
        "zh-TW": "有一筆血壓紀錄偏高，請查看",
        "en": "A blood pressure reading was high. Please check.",
        "id": "Satu catatan tekanan darah tinggi. Silakan periksa.",
        "vi": "Một chỉ số huyết áp cao. Vui lòng kiểm tra.",
        "th": "มีค่าความดันโลหิตสูง กรุณาตรวจสอบ",
        "ja": "血圧の記録が高めでした。ご確認ください。",
    },
    "flex.health_alert.alt.bp_low": {
        "zh-TW": "有一筆血壓紀錄偏低，請查看",
        "en": "A blood pressure reading was low. Please check.",
        "id": "Satu catatan tekanan darah rendah. Silakan periksa.",
        "vi": "Một chỉ số huyết áp thấp. Vui lòng kiểm tra.",
        "th": "มีค่าความดันโลหิตต่ำ กรุณาตรวจสอบ",
        "ja": "血圧の記録が低めでした。ご確認ください。",
    },
    "flex.health_alert.alt.glucose_high": {
        "zh-TW": "有一筆血糖紀錄偏高，請查看",
        "en": "A blood glucose reading was high. Please check.",
        "id": "Satu catatan gula darah tinggi. Silakan periksa.",
        "vi": "Một chỉ số đường huyết cao. Vui lòng kiểm tra.",
        "th": "มีค่าน้ำตาลในเลือดสูง กรุณาตรวจสอบ",
        "ja": "血糖値の記録が高めでした。ご確認ください。",
    },
    "flex.health_alert.alt.glucose_low": {
        "zh-TW": "有一筆血糖紀錄偏低，請查看",
        "en": "A blood glucose reading was low. Please check.",
        "id": "Satu catatan gula darah rendah. Silakan periksa.",
        "vi": "Một chỉ số đường huyết thấp. Vui lòng kiểm tra.",
        "th": "มีค่าน้ำตาลในเลือดต่ำ กรุณาตรวจสอบ",
        "ja": "血糖値の記録が低めでした。ご確認ください。",
    },
    "flex.health_alert.field.systolic": {
        "zh-TW": "收縮壓", "en": "Systolic", "id": "Sistolik",
        "vi": "Tâm thu", "th": "ความดันช่วงบน", "ja": "収縮期血圧",
    },
    "flex.health_alert.field.diastolic": {
        "zh-TW": "舒張壓", "en": "Diastolic", "id": "Diastolik",
        "vi": "Tâm trương", "th": "ความดันช่วงล่าง", "ja": "拡張期血圧",
    },
    "flex.health_alert.field.pulse": {
        "zh-TW": "脈搏", "en": "Pulse", "id": "Denyut nadi",
        "vi": "Mạch", "th": "ชีพจร", "ja": "脈拍",
    },
    "flex.health_alert.field.glucose": {
        "zh-TW": "血糖", "en": "Blood glucose", "id": "Gula darah",
        "vi": "Đường huyết", "th": "น้ำตาลในเลือด", "ja": "血糖値",
    },
    "flex.health_alert.meal.fasting": {
        "zh-TW": "空腹", "en": "Fasting", "id": "Puasa",
        "vi": "Lúc đói", "th": "ขณะท้องว่าง", "ja": "空腹時",
    },
    "flex.health_alert.meal.before_meal": {
        "zh-TW": "飯前", "en": "Before meal", "id": "Sebelum makan",
        "vi": "Trước ăn", "th": "ก่อนอาหาร", "ja": "食前",
    },
    "flex.health_alert.meal.after_meal": {
        "zh-TW": "飯後", "en": "After meal", "id": "Sesudah makan",
        "vi": "Sau ăn", "th": "หลังอาหาร", "ja": "食後",
    },
    "flex.health_alert.meal.bedtime": {
        "zh-TW": "睡前", "en": "Bedtime", "id": "Sebelum tidur",
        "vi": "Trước khi ngủ", "th": "ก่อนนอน", "ja": "就寝前",
    },
    "flex.health_alert.meal.random": {
        "zh-TW": "隨機", "en": "Random", "id": "Acak",
        "vi": "Ngẫu nhiên", "th": "สุ่ม", "ja": "ランダム",
    },
    "flex.health_alert.label.measured_at": {
        "zh-TW": "量測時間", "en": "Measured at", "id": "Waktu pengukuran",
        "vi": "Thời điểm đo", "th": "เวลาที่วัด", "ja": "測定時刻",
    },
    "flex.health_alert.exceeded.upper": {
        "zh-TW": "超過上限 {bound}",
        "en": "Above the upper limit of {bound}",
        "id": "Melebihi batas atas {bound}",
        "vi": "Vượt giới hạn trên {bound}",
        "th": "เกินขีดจำกัดบน {bound}",
        "ja": "上限 {bound} を超えています",
    },
    "flex.health_alert.exceeded.lower": {
        "zh-TW": "低於下限 {bound}",
        "en": "Below the lower limit of {bound}",
        "id": "Di bawah batas bawah {bound}",
        "vi": "Dưới giới hạn dưới {bound}",
        "th": "ต่ำกว่าขีดจำกัดล่าง {bound}",
        "ja": "下限 {bound} を下回っています",
    },
    "flex.health_alert.recorded_by": {
        "zh-TW": "由 {name} 記錄",
        "en": "Recorded by {name}",
        "id": "Dicatat oleh {name}",
        "vi": "Được {name} ghi lại",
        "th": "บันทึกโดย {name}",
        "ja": "{name} が記録しました",
    },
    "flex.health_alert.button.open_records": {
        "zh-TW": "查看健康紀錄",
        "en": "View health records",
        "id": "Lihat catatan kesehatan",
        "vi": "Xem hồ sơ sức khỏe",
        "th": "ดูบันทึกสุขภาพ",
        "ja": "健康記録を見る",
    },
    # --- 經期異常推播（health-alerts spec「經期異常只通知本人」）---
    #
    # 文字 SHALL NOT 含「經期」「月經」（或其他五語系的對應詞）或任何數值——
    # LINE 推播會出現在鎖定畫面預覽，長輩常與家人共用手機
    # （tests/unit/services/line_messaging/flex/test_menstrual_alert_flex.py
    # 逐語系檢查這一點）。
    "flex.menstrual_alert.header": {
        "zh-TW": "健康提醒",
        "en": "Health reminder",
        "id": "Pengingat kesehatan",
        "vi": "Nhắc nhở sức khỏe",
        "th": "การแจ้งเตือนสุขภาพ",
        "ja": "健康のお知らせ",
    },
    "flex.menstrual_alert.body": {
        "zh-TW": "有一筆健康紀錄需要留意，建議您至 CARE 查看。",
        "en": "One of your health records needs attention. Please check it in CARE.",
        "id": "Ada catatan kesehatan yang perlu diperhatikan. Silakan periksa di CARE.",
        "vi": "Có một mục sức khỏe cần bạn lưu ý. Vui lòng kiểm tra trong CARE.",
        "th": "มีบันทึกสุขภาพที่ควรให้ความสนใจ กรุณาตรวจสอบใน CARE",
        "ja": "確認が必要な健康記録があります。CARE でご確認ください。",
    },
    "flex.menstrual_alert.alt": {
        "zh-TW": "有一筆健康紀錄需要留意",
        "en": "A health record needs attention",
        "id": "Ada catatan kesehatan yang perlu diperhatikan",
        "vi": "Có một mục sức khỏe cần lưu ý",
        "th": "มีบันทึกสุขภาพที่ควรให้ความสนใจ",
        "ja": "確認が必要な健康記録があります",
    },
    "flex.menstrual_alert.button": {
        "zh-TW": "前往查看",
        "en": "Open CARE",
        "id": "Buka CARE",
        "vi": "Mở CARE",
        "th": "เปิด CARE",
        "ja": "CARE を開く",
    },
    # --- LINE 進站流程 ---
    #
    # agent 超過 AGENT_TOTAL_TIMEOUT_SECONDS 還沒回：不是「發生錯誤」（那句會讓
    # 人以為問題本身有問題），而是這一輪太久、請再問一次。
    "line.fallback_busy": {
        "zh-TW": "抱歉，這個問題處理得比較久，還沒有結果。請稍後再問一次。",
        "en": "Sorry, this is taking longer than expected and hasn't finished. Please ask again in a moment.",
        "id": "Maaf, pertanyaan ini butuh waktu lebih lama dan belum selesai. Silakan tanyakan lagi sebentar lagi.",
        "vi": "Xin lỗi, câu hỏi này mất nhiều thời gian hơn dự kiến và chưa có kết quả. Vui lòng hỏi lại sau ít phút.",
        "th": "ขออภัย คำถามนี้ใช้เวลานานกว่าปกติและยังไม่เสร็จ กรุณาถามใหม่อีกครั้งในอีกสักครู่",
        "ja": "申し訳ありません。処理に時間がかかっており、まだ結果が出ていません。しばらくしてからもう一度お尋ねください。",
    },
    # 媒體訊息（圖片／語音／影片／檔案）辨識失敗的四種情況，分開講：
    # 太大與不支援是使用者能自己改的；服務失敗要請他等一下再傳，不是重拍；
    # 真的沒有內容才請他確認清晰度。{kind} 帶入 media.kind.* 的譯名。
    "media.kind.image": {
        "zh-TW": "圖片", "en": "image", "id": "gambar", "vi": "hình ảnh", "th": "รูปภาพ", "ja": "画像",
    },
    "media.kind.audio": {
        "zh-TW": "語音", "en": "voice message", "id": "pesan suara", "vi": "tin nhắn thoại", "th": "ข้อความเสียง", "ja": "音声",
    },
    "media.kind.video": {
        "zh-TW": "影片", "en": "video", "id": "video", "vi": "video", "th": "วิดีโอ", "ja": "動画",
    },
    "media.kind.file": {
        "zh-TW": "檔案", "en": "file", "id": "berkas", "vi": "tệp", "th": "ไฟล์", "ja": "ファイル",
    },
    "media.too_large": {
        "zh-TW": "您傳送的{kind}超過 {limit_mb} MB 的上限，請壓縮或裁切後再傳一次。",
        "en": "The {kind} you sent is over the {limit_mb} MB limit. Please compress or trim it and send it again.",
        "id": "{kind} yang Anda kirim melebihi batas {limit_mb} MB. Silakan kompres atau potong lalu kirim lagi.",
        "vi": "{kind} bạn gửi vượt quá giới hạn {limit_mb} MB. Vui lòng nén hoặc cắt bớt rồi gửi lại.",
        "th": "{kind} ที่คุณส่งเกินขีดจำกัด {limit_mb} MB กรุณาบีบอัดหรือตัดให้สั้นลงแล้วส่งใหม่",
        "ja": "送信された{kind}は上限 {limit_mb} MB を超えています。圧縮または短くしてから、もう一度送ってください。",
    },
    "media.unsupported": {
        "zh-TW": "抱歉，目前不支援這種{kind}格式。可以改傳圖片、語音，或 PDF／文字檔。",
        "en": "Sorry, this {kind} format isn't supported yet. Please send an image, a voice message, or a PDF/text file instead.",
        "id": "Maaf, format {kind} ini belum didukung. Silakan kirim gambar, pesan suara, atau berkas PDF/teks.",
        "vi": "Xin lỗi, định dạng {kind} này chưa được hỗ trợ. Vui lòng gửi hình ảnh, tin nhắn thoại hoặc tệp PDF/văn bản.",
        "th": "ขออภัย ยังไม่รองรับ{kind}รูปแบบนี้ กรุณาส่งรูปภาพ ข้อความเสียง หรือไฟล์ PDF/ข้อความแทน",
        "ja": "申し訳ありません。この{kind}の形式には対応していません。画像・音声、または PDF／テキストファイルでお送りください。",
    },
    "media.service_unavailable": {
        "zh-TW": "抱歉，{kind}辨識服務暫時無法使用，剛才那則沒有處理到。請過幾分鐘再傳一次。",
        "en": "Sorry, the {kind} recognition service is temporarily unavailable and your last message wasn't processed. Please send it again in a few minutes.",
        "id": "Maaf, layanan pengenalan {kind} sedang tidak tersedia dan pesan terakhir Anda belum diproses. Silakan kirim lagi beberapa menit lagi.",
        "vi": "Xin lỗi, dịch vụ nhận dạng {kind} tạm thời không khả dụng nên tin nhắn vừa rồi chưa được xử lý. Vui lòng gửi lại sau vài phút.",
        "th": "ขออภัย บริการรู้จำ{kind}ไม่พร้อมใช้งานชั่วคราว ข้อความล่าสุดของคุณจึงยังไม่ได้รับการประมวลผล กรุณาส่งใหม่ในอีกสักครู่",
        "ja": "申し訳ありません。{kind}の認識サービスが一時的に利用できず、先ほどのメッセージは処理できませんでした。数分後にもう一度お送りください。",
    },
    "media.no_content": {
        "zh-TW": "無法從您傳送的{kind}中辨識出任何文字，請確認內容清晰並重新傳送。",
        "en": "I couldn't find any text in the {kind} you sent. Please make sure it's clear and send it again.",
        "id": "Saya tidak dapat menemukan teks apa pun di {kind} yang Anda kirim. Pastikan isinya jelas lalu kirim lagi.",
        "vi": "Tôi không nhận ra được nội dung nào trong {kind} bạn gửi. Vui lòng kiểm tra cho rõ rồi gửi lại.",
        "th": "ไม่พบข้อความใด ๆ ใน{kind}ที่คุณส่ง กรุณาตรวจสอบให้ชัดเจนแล้วส่งใหม่",
        "ja": "送信された{kind}から文字を読み取れませんでした。内容がはっきり写っているか確認して、もう一度お送りください。",
    },
    # --- RAG／agent 管線 ---
    #
    # 網搜服務被限流（Firecrawl 回 429）。以前這條路被吞成 0 筆、對使用者說
    # 「找不到，請換個方式描述」——換十種說法都一樣找不到，因為根本沒搜。
    # 限流與一般失敗分開一個 key：使用者該做的是等一下再問，而不是換說法。
    "rag.fail.WEB_RATE_LIMITED": {
        "zh-TW": "網路搜尋服務目前查詢太頻繁，暫時無法使用。請過一會兒再問一次。",
        "en": "The web search service is temporarily unavailable due to too many requests. Please try again in a little while.",
        "id": "Layanan pencarian web sementara tidak tersedia karena terlalu banyak permintaan. Silakan coba lagi sebentar lagi.",
        "vi": "Dịch vụ tìm kiếm web tạm thời không khả dụng do có quá nhiều yêu cầu. Vui lòng thử lại sau ít phút.",
        "th": "บริการค้นหาเว็บไม่สามารถใช้งานได้ชั่วคราวเนื่องจากมีคำขอมากเกินไป กรุณาลองใหม่อีกครั้งในอีกสักครู่",
        "ja": "リクエストが集中しているため、ウェブ検索サービスを一時的に利用できません。しばらくしてからもう一度お試しください。",
    },
    # --- 走失求救與即時位置分享 ---
    #
    # 長輩端的字句是給正在慌的人看的：短句、一次只講一件事、先說「有人在幫你」
    # 再說要做什麼。家人端要在通知列上就看得出是誰、發生什麼事。
    "lost.elder.header.lost": {
        "zh-TW": "別擔心，正在通知你的家人",
        "en": "Don't worry, we're telling your family",
        "id": "Jangan khawatir, kami sedang memberi tahu keluarga Anda",
        "vi": "Đừng lo, chúng tôi đang báo cho gia đình bạn",
        "th": "ไม่ต้องกังวล กำลังแจ้งครอบครัวของคุณ",
        "ja": "大丈夫です。ご家族に知らせています",
    },
    "lost.elder.header.share": {
        "zh-TW": "正在把你的位置告訴家人",
        "en": "Sending your location to your family",
        "id": "Mengirim lokasi Anda ke keluarga",
        "vi": "Đang gửi vị trí của bạn cho gia đình",
        "th": "กำลังส่งตำแหน่งของคุณให้ครอบครัว",
        "ja": "ご家族に現在地を知らせています",
    },
    "lost.elder.header.active": {
        "zh-TW": "家人已經收到通知，正在找你",
        "en": "Your family has been told and is looking for you",
        "id": "Keluarga Anda sudah diberi tahu dan sedang mencari Anda",
        "vi": "Gia đình bạn đã được báo và đang tìm bạn",
        "th": "ครอบครัวได้รับแจ้งแล้วและกำลังตามหาคุณ",
        "ja": "ご家族に知らせました。探しに向かっています",
    },
    "lost.elder.body": {
        "zh-TW": "按下面的按鈕，家人就能在地圖上看到你在哪裡。",
        "en": "Tap the button below so your family can see where you are on a map.",
        "id": "Ketuk tombol di bawah agar keluarga dapat melihat lokasi Anda di peta.",
        "vi": "Bấm nút bên dưới để gia đình thấy bạn đang ở đâu trên bản đồ.",
        "th": "กดปุ่มด้านล่าง ครอบครัวจะเห็นว่าคุณอยู่ที่ไหนบนแผนที่",
        "ja": "下のボタンを押すと、ご家族が地図であなたの場所を見られます。",
    },
    "lost.elder.button": {
        "zh-TW": "讓家人看到我在哪裡",
        "en": "Show my family where I am",
        "id": "Tunjukkan lokasi saya ke keluarga",
        "vi": "Cho gia đình thấy tôi ở đâu",
        "th": "ให้ครอบครัวเห็นว่าฉันอยู่ที่ไหน",
        "ja": "家族に居場所を見せる",
    },
    "lost.elder.stay": {
        "zh-TW": "打開後請不要關掉畫面，留在原地等家人。",
        "en": "After it opens, keep the screen on and stay where you are.",
        "id": "Setelah terbuka, jangan tutup layarnya dan tetap di tempat.",
        "vi": "Sau khi mở, đừng tắt màn hình và hãy ở yên tại chỗ.",
        "th": "เมื่อเปิดแล้วอย่าปิดหน้าจอ และรออยู่ที่เดิม",
        "ja": "開いたら画面を閉じずに、その場で待っていてください。",
    },
    "lost.elder.fallback_hint": {
        "zh-TW": "按鈕打不開的話，可以按聊天室下方的「傳送一次位置」。",
        "en": "If the button doesn't open, tap \"Send location once\" at the bottom of the chat.",
        "id": "Jika tombol tidak terbuka, ketuk \"Kirim lokasi sekali\" di bagian bawah obrolan.",
        "vi": "Nếu nút không mở được, hãy bấm \"Gửi vị trí một lần\" ở cuối khung trò chuyện.",
        "th": "ถ้าปุ่มเปิดไม่ได้ ให้กด \"ส่งตำแหน่งครั้งเดียว\" ด้านล่างของแชท",
        "ja": "ボタンが開かないときは、トーク画面下の「現在地を1回送る」を押してください。",
    },
    "lost.elder.alt_text": {
        "zh-TW": "按這裡讓家人看到你在哪裡",
        "en": "Tap here so your family can see where you are",
        "id": "Ketuk di sini agar keluarga bisa melihat lokasi Anda",
        "vi": "Bấm vào đây để gia đình thấy bạn đang ở đâu",
        "th": "กดที่นี่เพื่อให้ครอบครัวเห็นว่าคุณอยู่ที่ไหน",
        "ja": "ここを押すと、ご家族があなたの場所を見られます",
    },
    # LINE 快速回覆的按鈕文字上限 20 字元。
    "lost.elder.quick_reply": {
        "zh-TW": "傳送一次位置",
        "en": "Send location once",
        "id": "Kirim lokasi sekali",
        "vi": "Gửi vị trí một lần",
        "th": "ส่งตำแหน่งครั้งเดียว",
        "ja": "現在地を1回送る",
    },
    "lost.elder.family_notified": {
        "zh-TW": "你的家人已經收到通知了。請留在原地，按上面的按鈕讓家人看到你在哪裡。",
        "en": "Your family has been notified. Please stay where you are and tap the button above so they can see where you are.",
        "id": "Keluarga Anda sudah diberi tahu. Tetaplah di tempat dan ketuk tombol di atas agar mereka bisa melihat lokasi Anda.",
        "vi": "Gia đình bạn đã nhận được thông báo. Hãy ở yên tại chỗ và bấm nút phía trên để họ thấy bạn đang ở đâu.",
        "th": "ครอบครัวของคุณได้รับการแจ้งเตือนแล้ว กรุณารออยู่ที่เดิม และกดปุ่มด้านบนเพื่อให้ครอบครัวเห็นว่าคุณอยู่ที่ไหน",
        "ja": "ご家族に通知が届きました。その場を離れず、上のボタンを押して居場所を知らせてください。",
    },
    "lost.elder.notify_failed": {
        "zh-TW": "通知家人沒有成功。請撥 110 報警，或請附近的店家、警察幫忙。",
        "en": "We couldn't reach your family. Please call 110 (police), or ask a nearby shop or police officer for help.",
        "id": "Kami tidak berhasil menghubungi keluarga Anda. Silakan telepon 110 (polisi), atau minta bantuan toko atau polisi terdekat.",
        "vi": "Chưa báo được cho gia đình bạn. Hãy gọi 110 (cảnh sát), hoặc nhờ cửa hàng hay cảnh sát gần đó giúp đỡ.",
        "th": "แจ้งครอบครัวไม่สำเร็จ กรุณาโทร 110 (ตำรวจ) หรือขอความช่วยเหลือจากร้านค้าหรือตำรวจใกล้ ๆ",
        "ja": "ご家族に知らせることができませんでした。110番に電話するか、近くのお店や警察官に助けを求めてください。",
    },
    "lost.elder.no_family.title": {
        "zh-TW": "請找人幫忙",
        "en": "Please ask someone for help",
        "id": "Silakan minta bantuan",
        "vi": "Hãy nhờ người giúp đỡ",
        "th": "กรุณาขอความช่วยเหลือ",
        "ja": "周りの人に助けを求めてください",
    },
    "lost.elder.no_family.body": {
        "zh-TW": "你還沒有加入家人，CARE 沒辦法幫你通知。請撥 110 報警，或把手機拿給附近的店家、警察看，請他們幫忙。",
        "en": "You haven't added any family members yet, so CARE can't notify anyone. Please call 110 (police), or show your phone to a nearby shop or police officer and ask for help.",
        "id": "Anda belum menambahkan anggota keluarga, jadi CARE tidak bisa memberi tahu siapa pun. Silakan telepon 110 (polisi), atau tunjukkan ponsel Anda ke toko atau polisi terdekat dan minta bantuan.",
        "vi": "Bạn chưa thêm người thân nên CARE không thể báo cho ai. Hãy gọi 110 (cảnh sát), hoặc đưa điện thoại cho cửa hàng hay cảnh sát gần đó xem và nhờ họ giúp.",
        "th": "คุณยังไม่ได้เพิ่มสมาชิกครอบครัว CARE จึงแจ้งใครไม่ได้ กรุณาโทร 110 (ตำรวจ) หรือยื่นโทรศัพท์ให้ร้านค้าหรือตำรวจใกล้ ๆ ดูและขอความช่วยเหลือ",
        "ja": "まだご家族が登録されていないため、CARE から知らせることができません。110番に電話するか、近くのお店や警察官にこの画面を見せて助けを求めてください。",
    },
    "lost.elder.call_110": {
        "zh-TW": "撥打 110",
        "en": "Call 110",
        "id": "Telepon 110",
        "vi": "Gọi 110",
        "th": "โทร 110",
        "ja": "110番に電話",
    },
    "lost.elder.location_received": {
        "zh-TW": "已經把你的位置傳給家人了，請留在原地。想讓家人一直看到你的位置，請按上面的「讓家人看到我在哪裡」。",
        "en": "Your location has been sent to your family. Please stay where you are. To keep them updated, tap \"Show my family where I am\" above.",
        "id": "Lokasi Anda sudah dikirim ke keluarga. Tetaplah di tempat. Agar mereka terus melihat lokasi Anda, ketuk \"Tunjukkan lokasi saya ke keluarga\" di atas.",
        "vi": "Đã gửi vị trí của bạn cho gia đình. Hãy ở yên tại chỗ. Để gia đình luôn thấy vị trí của bạn, hãy bấm \"Cho gia đình thấy tôi ở đâu\" ở trên.",
        "th": "ส่งตำแหน่งของคุณให้ครอบครัวแล้ว กรุณารออยู่ที่เดิม ถ้าต้องการให้ครอบครัวเห็นตำแหน่งตลอด ให้กด \"ให้ครอบครัวเห็นว่าฉันอยู่ที่ไหน\" ด้านบน",
        "ja": "現在地をご家族に送りました。その場で待っていてください。ずっと居場所を知らせるには、上の「家族に居場所を見せる」を押してください。",
    },
    "lost.elder.reopen.header": {
        "zh-TW": "家人還在找你",
        "en": "Your family is still looking for you",
        "id": "Keluarga Anda masih mencari Anda",
        "vi": "Gia đình vẫn đang tìm bạn",
        "th": "ครอบครัวยังตามหาคุณอยู่",
        "ja": "ご家族がまだ探しています",
    },
    "lost.elder.reopen.body": {
        "zh-TW": "你的位置停止更新了。請再按一次下面的按鈕，打開後不要關掉畫面。",
        "en": "Your location has stopped updating. Please tap the button below again and keep the screen on.",
        "id": "Lokasi Anda berhenti diperbarui. Ketuk lagi tombol di bawah dan jangan tutup layarnya.",
        "vi": "Vị trí của bạn đã ngừng cập nhật. Hãy bấm lại nút bên dưới và đừng tắt màn hình.",
        "th": "ตำแหน่งของคุณหยุดอัปเดตแล้ว กรุณากดปุ่มด้านล่างอีกครั้ง และอย่าปิดหน้าจอ",
        "ja": "現在地の更新が止まっています。もう一度下のボタンを押して、画面を閉じないでください。",
    },
    "lost.elder.found": {
        "zh-TW": "{finder}說已經找到你了，位置分享已經停止。",
        "en": "{finder} says they've found you. Location sharing has stopped.",
        "id": "{finder} bilang sudah menemukan Anda. Berbagi lokasi telah dihentikan.",
        "vi": "{finder} cho biết đã tìm thấy bạn. Đã dừng chia sẻ vị trí.",
        "th": "{finder} บอกว่าพบคุณแล้ว หยุดแชร์ตำแหน่งแล้ว",
        "ja": "{finder}さんがあなたを見つけました。位置の共有を終了しました。",
    },
    "lost.elder.auto_ended": {
        "zh-TW": "位置分享已經自動停止。如果還需要幫忙，再跟我說「我走丟了」，或撥 110。",
        "en": "Location sharing has stopped automatically. If you still need help, tell me \"I'm lost\" again, or call 110.",
        "id": "Berbagi lokasi otomatis dihentikan. Jika masih butuh bantuan, katakan lagi \"saya tersesat\", atau telepon 110.",
        "vi": "Chia sẻ vị trí đã tự động dừng. Nếu vẫn cần giúp, hãy nói lại \"tôi bị lạc\", hoặc gọi 110.",
        "th": "การแชร์ตำแหน่งหยุดโดยอัตโนมัติแล้ว ถ้ายังต้องการความช่วยเหลือ ให้บอกว่า \"ฉันหลงทาง\" อีกครั้ง หรือโทร 110",
        "ja": "位置の共有は自動的に終了しました。まだ助けが必要なら、もう一度「迷子になった」と送るか、110番に電話してください。",
    },
    # 家人在地圖頁按了「我去找他」，推給長輩一次（見 LostLocationService.notify_elder_family_coming）。
    "lost.elder.family_coming": {
        "zh-TW": "{name}正在過來找你，請待在原地。",
        "en": "{name} is on the way to find you. Please stay where you are.",
        "id": "{name} sedang menuju ke tempat Anda. Tetaplah di tempat.",
        "vi": "{name} đang đến tìm bạn. Hãy ở yên tại chỗ.",
        "th": "{name} กำลังมาหาคุณ กรุณารออยู่ที่เดิม",
        "ja": "{name}さんが迎えに向かっています。その場で待っていてください。",
    },
    # 走失分類器沒把握時，回覆下方的快速回覆（見 app/services/lost/lost_classifier.py）。
    # 按鈕文字上限 20 字元（LINE quick reply label）。
    "lost.help.quick_reply": {
        "zh-TW": "我迷路了，通知家人",
        "en": "Lost? Tell my family",
        "id": "Saya tersesat",
        "vi": "Tôi bị lạc, báo nhà",
        "th": "หลงทาง แจ้งครอบครัว",
        "ja": "迷子です、家族に連絡",
    },
    # 按下之後，聊天室裡以使用者身分顯示的那句話。
    "lost.help.display": {
        "zh-TW": "我迷路了，請通知家人",
        "en": "I'm lost, please tell my family",
        "id": "Saya tersesat, tolong beri tahu keluarga saya",
        "vi": "Tôi bị lạc, hãy báo cho gia đình tôi",
        "th": "ฉันหลงทาง ช่วยแจ้งครอบครัวด้วย",
        "ja": "道に迷いました。家族に知らせてください",
    },
    "lost.family.title.lost": {
        "zh-TW": "走失求救",
        "en": "Lost: needs help",
        "id": "Tersesat: butuh bantuan",
        "vi": "Bị lạc: cần giúp đỡ",
        "th": "หลงทาง: ต้องการความช่วยเหลือ",
        "ja": "迷子の連絡",
    },
    "lost.family.title.share": {
        "zh-TW": "位置分享",
        "en": "Location shared",
        "id": "Lokasi dibagikan",
        "vi": "Chia sẻ vị trí",
        "th": "แชร์ตำแหน่ง",
        "ja": "位置の共有",
    },
    "lost.family.lead.lost": {
        "zh-TW": "{name}剛剛在 CARE 說自己走丟了。",
        "en": "{name} just told CARE they are lost.",
        "id": "{name} baru saja memberi tahu CARE bahwa dirinya tersesat.",
        "vi": "{name} vừa nói với CARE rằng mình bị lạc.",
        "th": "{name} เพิ่งบอก CARE ว่าหลงทาง",
        "ja": "{name}さんが CARE で「迷子になった」と伝えてきました。",
    },
    "lost.family.lead.share": {
        "zh-TW": "{name}想讓家人知道自己現在在哪裡。",
        "en": "{name} wants the family to know where they are right now.",
        "id": "{name} ingin keluarga tahu lokasinya saat ini.",
        "vi": "{name} muốn gia đình biết mình đang ở đâu.",
        "th": "{name} อยากให้ครอบครัวรู้ว่าตอนนี้อยู่ที่ไหน",
        "ja": "{name}さんが今いる場所をご家族に知らせたいそうです。",
    },
    "lost.family.words_label": {
        "zh-TW": "{name}說的話",
        "en": "What {name} said",
        "id": "Yang dikatakan {name}",
        "vi": "Lời {name} nói",
        "th": "สิ่งที่ {name} พูด",
        "ja": "{name}さんの言葉",
    },
    "lost.family.waiting": {
        "zh-TW": "正在等{name}打開定位畫面，收到位置後會再通知你。",
        "en": "Waiting for {name} to open the location page. You'll be notified when the location arrives.",
        "id": "Menunggu {name} membuka halaman lokasi. Anda akan diberi tahu saat lokasinya masuk.",
        "vi": "Đang chờ {name} mở trang vị trí. Bạn sẽ được báo khi nhận được vị trí.",
        "th": "กำลังรอ {name} เปิดหน้าตำแหน่ง เมื่อได้รับตำแหน่งแล้วจะแจ้งคุณอีกครั้ง",
        "ja": "{name}さんが位置の画面を開くのを待っています。位置が届いたらお知らせします。",
    },
    "lost.family.view_map": {
        "zh-TW": "看即時位置",
        "en": "View live location",
        "id": "Lihat lokasi langsung",
        "vi": "Xem vị trí trực tiếp",
        "th": "ดูตำแหน่งแบบสด",
        "ja": "現在地を見る",
    },
    "lost.family.navigate": {
        "zh-TW": "導航到最後位置",
        "en": "Navigate to last location",
        "id": "Navigasi ke lokasi terakhir",
        "vi": "Chỉ đường đến vị trí cuối",
        "th": "นำทางไปตำแหน่งล่าสุด",
        "ja": "最後の位置へ案内",
    },
    "lost.family.footer": {
        "zh-TW": "位置來自{name}的手機，可能有幾十公尺誤差。聯絡不上又找不到人時，請撥 110。",
        "en": "The location comes from {name}'s phone and may be off by tens of meters. If you can't reach or find them, call 110.",
        "id": "Lokasi berasal dari ponsel {name} dan bisa meleset puluhan meter. Jika tidak bisa dihubungi atau ditemukan, telepon 110.",
        "vi": "Vị trí lấy từ điện thoại của {name}, có thể sai lệch vài chục mét. Nếu không liên lạc được hoặc không tìm thấy, hãy gọi 110.",
        "th": "ตำแหน่งมาจากโทรศัพท์ของ {name} อาจคลาดเคลื่อนหลายสิบเมตร ถ้าติดต่อไม่ได้หรือหาไม่เจอ กรุณาโทร 110",
        "ja": "位置は{name}さんのスマートフォンから届いたもので、数十メートルずれることがあります。連絡がつかず見つからないときは110番に電話してください。",
    },
    "lost.family.alt_text.lost": {
        "zh-TW": "{name}說自己走丟了",
        "en": "{name} says they are lost",
        "id": "{name} bilang dirinya tersesat",
        "vi": "{name} nói mình bị lạc",
        "th": "{name} บอกว่าหลงทาง",
        "ja": "{name}さんが迷子になったと連絡してきました",
    },
    "lost.family.alt_text.share": {
        "zh-TW": "{name}想讓你知道自己在哪裡",
        "en": "{name} wants you to know where they are",
        "id": "{name} ingin Anda tahu lokasinya",
        "vi": "{name} muốn bạn biết mình đang ở đâu",
        "th": "{name} อยากให้คุณรู้ว่าอยู่ที่ไหน",
        "ja": "{name}さんが居場所を知らせています",
    },
    "lost.family.started.alt_text": {
        "zh-TW": "已經收到{name}的位置",
        "en": "{name}'s location has arrived",
        "id": "Lokasi {name} sudah masuk",
        "vi": "Đã nhận được vị trí của {name}",
        "th": "ได้รับตำแหน่งของ {name} แล้ว",
        "ja": "{name}さんの位置が届きました",
    },
    "lost.family.started.body": {
        "zh-TW": "點下面的按鈕看地圖，位置會自動更新。",
        "en": "Tap the button below to open the map. It updates automatically.",
        "id": "Ketuk tombol di bawah untuk membuka peta. Lokasinya diperbarui otomatis.",
        "vi": "Bấm nút bên dưới để mở bản đồ. Vị trí sẽ tự động cập nhật.",
        "th": "กดปุ่มด้านล่างเพื่อดูแผนที่ ตำแหน่งจะอัปเดตอัตโนมัติ",
        "ja": "下のボタンで地図を開けます。位置は自動で更新されます。",
    },
    "lost.family.stale.alt_text": {
        "zh-TW": "{name}的位置停止更新了",
        "en": "{name}'s location stopped updating",
        "id": "Lokasi {name} berhenti diperbarui",
        "vi": "Vị trí của {name} đã ngừng cập nhật",
        "th": "ตำแหน่งของ {name} หยุดอัปเดตแล้ว",
        "ja": "{name}さんの位置の更新が止まりました",
    },
    "lost.family.stale.body": {
        "zh-TW": "已經 {minutes} 分鐘沒有收到新位置，可能是畫面被關掉或手機沒訊號。地圖上是最後收到的位置。",
        "en": "No new location for {minutes} minutes. The page may have been closed or the phone may have lost signal. The map shows the last location received.",
        "id": "Tidak ada lokasi baru selama {minutes} menit. Halaman mungkin tertutup atau ponsel kehilangan sinyal. Peta menampilkan lokasi terakhir yang diterima.",
        "vi": "Đã {minutes} phút không nhận được vị trí mới. Có thể màn hình đã bị tắt hoặc điện thoại mất sóng. Bản đồ hiển thị vị trí nhận được gần nhất.",
        "th": "ไม่ได้รับตำแหน่งใหม่มา {minutes} นาทีแล้ว อาจปิดหน้าจอไปหรือโทรศัพท์ไม่มีสัญญาณ แผนที่แสดงตำแหน่งล่าสุดที่ได้รับ",
        "ja": "{minutes}分間、新しい位置が届いていません。画面が閉じられたか、電波が届いていない可能性があります。地図には最後に届いた位置を表示しています。",
    },
    "lost.family.found": {
        "zh-TW": "{finder}已經找到{name}了，位置分享已經停止。",
        "en": "{finder} has found {name}. Location sharing has stopped.",
        "id": "{finder} sudah menemukan {name}. Berbagi lokasi telah dihentikan.",
        "vi": "{finder} đã tìm thấy {name}. Đã dừng chia sẻ vị trí.",
        "th": "{finder} พบ {name} แล้ว หยุดแชร์ตำแหน่งแล้ว",
        "ja": "{finder}さんが{name}さんを見つけました。位置の共有を終了しました。",
    },
    "lost.family.elder_safe": {
        "zh-TW": "{name}說自己已經安全了，位置分享已經停止。",
        "en": "{name} says they are safe now. Location sharing has stopped.",
        "id": "{name} bilang dirinya sudah aman. Berbagi lokasi telah dihentikan.",
        "vi": "{name} cho biết mình đã an toàn. Đã dừng chia sẻ vị trí.",
        "th": "{name} บอกว่าปลอดภัยแล้ว หยุดแชร์ตำแหน่งแล้ว",
        "ja": "{name}さんから「もう安全です」と連絡がありました。位置の共有を終了しました。",
    },
    "lost.family.auto_ended": {
        "zh-TW": "{name}的位置分享已經超過 {hours} 小時，自動停止了。如果還沒找到人，請撥 110 報警。",
        "en": "{name}'s location sharing passed {hours} hours and stopped automatically. If they still haven't been found, call 110.",
        "id": "Berbagi lokasi {name} sudah lebih dari {hours} jam dan berhenti otomatis. Jika belum ditemukan, telepon 110.",
        "vi": "Chia sẻ vị trí của {name} đã quá {hours} giờ nên tự động dừng. Nếu vẫn chưa tìm thấy, hãy gọi 110.",
        "th": "การแชร์ตำแหน่งของ {name} เกิน {hours} ชั่วโมงแล้วจึงหยุดอัตโนมัติ ถ้ายังหาไม่เจอ กรุณาโทร 110",
        "ja": "{name}さんの位置の共有は{hours}時間を過ぎたため自動的に終了しました。まだ見つかっていない場合は110番に電話してください。",
    },
    "lost.family.someone": {
        "zh-TW": "一位家人",
        "en": "A family member",
        "id": "Seorang anggota keluarga",
        "vi": "Một người thân",
        "th": "สมาชิกในครอบครัว",
        "ja": "ご家族の方",
    },
    # --- 諮詢摘要下載（純文字檔的檔頭） ------------------------------------
    #
    # 標題與 LIFF 的 consultRecord.summaryTitle 用同一組譯文，兩邊用詞一致。
    "consultation_export.title": {
        "zh-TW": "醫療諮詢紀錄摘要",
        "en": "Medical Consultation Summary",
        "id": "Ringkasan Konsultasi Medis",
        "vi": "Tóm tắt tư vấn y tế",
        "th": "สรุปการปรึกษาทางการแพทย์",
        "ja": "医療相談記録の要約",
    },
    "consultation_export.exported_at": {
        "zh-TW": "匯出時間：",
        "en": "Exported at: ",
        "id": "Diekspor pada: ",
        "vi": "Thời gian xuất: ",
        "th": "ส่งออกเมื่อ: ",
        "ja": "出力日時：",
    },
    "consultation_export.empty": {
        "zh-TW": "目前沒有摘要資料",
        "en": "No summary data available.",
        "id": "Belum ada data ringkasan.",
        "vi": "Hiện không có dữ liệu tóm tắt.",
        "th": "ยังไม่มีข้อมูลสรุป",
        "ja": "要約データがありません。",
    },
    # --- 摘要欄位名稱（各語言對照） ----
    "summary_field.health_issue": {
        "zh-TW": "健康問題",
        "en": "Health Issue",
        "id": "Masalah Kesehatan",
        "vi": "Vấn Đề Sức Khỏe",
        "th": "ปัญหาสุขภาพ",
        "ja": "健康上の問題",
    },
    "summary_field.medications_and_appointments": {
        "zh-TW": "用藥與掛號紀錄",
        "en": "Medications and Appointments",
        "id": "Obat dan Catatan Janji Temu",
        "vi": "Thuốc và Ghi Chép Khám Bệnh",
        "th": "ยาและบันทึกการนัดหมาย",
        "ja": "薬と診察記録",
    },
    "summary_field.recommendations": {
        "zh-TW": "建議",
        "en": "Recommendations",
        "id": "Rekomendasi",
        "vi": "Khuyến Cáo",
        "th": "ข้อเสนอแนะ",
        "ja": "推奨",
    },
    "summary_field.key_safety_alerts": {
        "zh-TW": "關鍵情況與安全提醒",
        "en": "Key Safety Alerts",
        "id": "Peringatan Keselamatan Utama",
        "vi": "Cảnh Báo An Toàn Chính",
        "th": "การแจ้งเตือนความปลอดภัยหลัก",
        "ja": "主要な安全警告",
    },
    "summary_field.other": {
        "zh-TW": "其他",
        "en": "Other",
        "id": "Lainnya",
        "vi": "Khác",
        "th": "อื่นๆ",
        "ja": "その他",
    },
    "summary_field.ai_summary": {
        "zh-TW": "AI小摘要",
        "en": "AI Summary",
        "id": "Ringkasan AI",
        "vi": "Tóm Tắt AI",
        "th": "สรุป AI",
        "ja": "AIの要約",
    },
    # --- 對話紀錄裡的工具卡片（原始對話頁與摘要對話稿） ------------------
    #
    # 卡片存的是整包 Flex JSON，顯示時依卡片頂層的結構化 key 換成一行字。
    # 摘要對話稿固定用 zh-TW（摘要 prompt 以中文撰寫並引用 risk_alert 這句）。
    "consultation_card.separator": {
        "zh-TW": "｜",
        "en": " | ",
        "id": " | ",
        "vi": " | ",
        "th": " | ",
        "ja": "｜",
    },
    "consultation_card.list_separator": {
        "zh-TW": "、",
        "en": ", ",
        "id": ", ",
        "vi": ", ",
        "th": ", ",
        "ja": "、",
    },
    "consultation_card.risk_alert": {
        "zh-TW": "觸發風險警示",
        "en": "Risk alert triggered",
        "id": "Peringatan risiko terpicu",
        "vi": "Đã kích hoạt cảnh báo rủi ro",
        "th": "มีการแจ้งเตือนความเสี่ยง",
        "ja": "リスク警告が発動",
    },
    "consultation_card.risk_alert.user_words": {
        "zh-TW": "使用者輸入：「{words}」",
        "en": 'User said: "{words}"',
        "id": 'Pengguna menulis: "{words}"',
        "vi": 'Người dùng nhập: "{words}"',
        "th": 'ผู้ใช้พิมพ์ว่า: "{words}"',
        "ja": "ユーザーの入力：「{words}」",
    },
    "consultation_card.risk_alert.reason": {
        "zh-TW": "判定原因：{reason}",
        "en": "Reason: {reason}",
        "id": "Alasan: {reason}",
        "vi": "Lý do: {reason}",
        "th": "เหตุผล: {reason}",
        "ja": "判定理由：{reason}",
    },
    "consultation_card.symptom_department": {
        "zh-TW": "科別建議卡",
        "en": "Department suggestion card",
        "id": "Kartu saran poli",
        "vi": "Thẻ gợi ý chuyên khoa",
        "th": "การ์ดแนะนำแผนก",
        "ja": "診療科提案カード",
    },
    "consultation_card.symptom_department.suggestion": {
        "zh-TW": "建議科別：{departments}",
        "en": "Suggested departments: {departments}",
        "id": "Poli yang disarankan: {departments}",
        "vi": "Chuyên khoa gợi ý: {departments}",
        "th": "แผนกที่แนะนำ: {departments}",
        "ja": "おすすめの診療科：{departments}",
    },
    "consultation_card.symptom_department.fallback": {
        "zh-TW": "系統無法判斷症狀，初診方向：{departments}",
        "en": "Symptom could not be determined; suggested first visit: {departments}",
        "id": "Gejala tidak dapat ditentukan; arahan kunjungan awal: {departments}",
        "vi": "Không xác định được triệu chứng; hướng khám ban đầu: {departments}",
        "th": "ระบบระบุอาการไม่ได้ แนะนำให้เริ่มตรวจที่: {departments}",
        "ja": "症状を判断できませんでした。初診の目安：{departments}",
    },
    "consultation_card.facilities": {
        "zh-TW": "院所查詢卡",
        "en": "Medical facility search card",
        "id": "Kartu pencarian fasilitas kesehatan",
        "vi": "Thẻ tra cứu cơ sở y tế",
        "th": "การ์ดค้นหาสถานพยาบาล",
        "ja": "医療機関検索カード",
    },
    "consultation_card.facilities.names": {
        "zh-TW": "院所：{names}",
        "en": "Facilities: {names}",
        "id": "Fasilitas: {names}",
        "vi": "Cơ sở: {names}",
        "th": "สถานพยาบาล: {names}",
        "ja": "医療機関：{names}",
    },
    "consultation_card.claim_verdict": {
        "zh-TW": "查核判定卡",
        "en": "Fact-check card",
        "id": "Kartu cek fakta",
        "vi": "Thẻ kiểm chứng thông tin",
        "th": "การ์ดตรวจสอบข้อเท็จจริง",
        "ja": "ファクトチェックカード",
    },
    "consultation_card.claim_verdict.verdict": {
        "zh-TW": "判定結果：{verdict}",
        "en": "Verdict: {verdict}",
        "id": "Hasil: {verdict}",
        "vi": "Kết luận: {verdict}",
        "th": "ผลการตรวจสอบ: {verdict}",
        "ja": "判定結果：{verdict}",
    },
    # 判定字樣是查核資料本身（matcher._VALID_VERDICTS 的五種），以原文當 key；
    # 查不到翻譯時顯示原文。
    "consultation_card.verdict.錯誤": {
        "zh-TW": "錯誤",
        "en": "False",
        "id": "Salah",
        "vi": "Sai",
        "th": "เท็จ",
        "ja": "誤り",
    },
    "consultation_card.verdict.部分錯誤": {
        "zh-TW": "部分錯誤",
        "en": "Partly false",
        "id": "Sebagian salah",
        "vi": "Sai một phần",
        "th": "เท็จบางส่วน",
        "ja": "一部誤り",
    },
    "consultation_card.verdict.正確": {
        "zh-TW": "正確",
        "en": "True",
        "id": "Benar",
        "vi": "Đúng",
        "th": "จริง",
        "ja": "正確",
    },
    "consultation_card.verdict.事實釐清": {
        "zh-TW": "事實釐清",
        "en": "Clarification",
        "id": "Klarifikasi",
        "vi": "Làm rõ sự thật",
        "th": "ชี้แจงข้อเท็จจริง",
        "ja": "事実の明確化",
    },
    "consultation_card.verdict.證據不足": {
        "zh-TW": "證據不足",
        "en": "Insufficient evidence",
        "id": "Bukti tidak cukup",
        "vi": "Không đủ bằng chứng",
        "th": "หลักฐานไม่เพียงพอ",
        "ja": "証拠不十分",
    },
    "consultation_card.official_site": {
        "zh-TW": "官網入口卡",
        "en": "Official site card",
        "id": "Kartu situs resmi",
        "vi": "Thẻ trang chính thức",
        "th": "การ์ดเว็บไซต์ทางการ",
        "ja": "公式サイトカード",
    },
    "consultation_card.unknown": {
        "zh-TW": "[系統卡片]",
        "en": "[System card]",
        "id": "[Kartu sistem]",
        "vi": "[Thẻ hệ thống]",
        "th": "[การ์ดระบบ]",
        "ja": "[システムカード]",
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


def subgroup_label(subgroup: str, language: str | None = None) -> str:
    """Translate a symptom-card subspecialty, falling back to the source label."""
    translations = _MESSAGES.get(f"subgroup.{subgroup}")
    if not translations:
        return department_label(subgroup, language)
    lang = get_request_language() if language is None else normalize_user_language(language)
    return translations.get(lang) or subgroup


_SYMPTOM_FALLBACK_REASON_KEYS = {
    "無法對應到已知的症狀條目": "flex.symptom.fallback_reason.unknown",
    "這個症狀可能牽涉多個科別": "flex.symptom.fallback_reason.broad",
    "這個症狀在對照表中只列了兒科": "flex.symptom.fallback_reason.pediatric_only",
}


def symptom_fallback_reason(reason: str | None, language: str | None = None) -> str:
    """Localize the finite service reasons without exposing unknown Chinese text."""
    lang = get_request_language() if language is None else normalize_user_language(language)
    if lang == DEFAULT_USER_LANGUAGE and reason:
        return reason
    key = _SYMPTOM_FALLBACK_REASON_KEYS.get(
        reason or "", "flex.symptom.fallback_reason.generic"
    )
    return t(key, lang)


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


def insert_before_sources(text: str, notice: str) -> str:
    """把一段提醒插在「參考資料來源」標題之前，沒有來源段落時直接接在最後。

    位置是關鍵而不是美觀問題：`reply.py._build_answer_card` 組卡片時會呼叫
    `strip_sources_section`，而它回傳的是**來源標題之前**的全部內容。提醒
    若接在整段最後面，純文字回覆看得到，卡片卻永遠看不到——而卡片才是絕大
    多數使用者實際看到的東西。

    住在這裡而不是 agent.py：`MedicationQuestionService` 也要把它算出來的
    服藥時間事實插在同一個位置，而那是在工具裡、不是在圖節點裡。
    """
    if not notice:
        return text
    split = split_at_sources_heading(text)
    if split is None:
        return f"{text}\n\n{notice}"
    heading, sources_body = split
    before, _ = text.split(heading, 1)
    return f"{before.rstrip()}\n\n{notice}\n\n{heading}{sources_body}"


def strip_sources_section(text: str) -> str:
    split = split_at_sources_heading(text)
    if split is None:
        return text
    heading, _ = split
    before, _ = text.split(heading, 1)
    return before.rstrip()
