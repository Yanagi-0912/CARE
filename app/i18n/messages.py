"""Localized user-facing message catalog."""

from __future__ import annotations

from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    SUPPORTED_LANGUAGES,
    get_request_language,
    normalize_user_language,
)

_MESSAGES: dict[str, dict[str, str]] = {
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
    "flex.detail.day_closed": {
        "zh-TW": "休診",
        "en": "Closed",
        "id": "Tutup",
        "vi": "Nghỉ",
        "th": "ปิด",
        "ja": "休診",
    },
    "flex.detail.no_data": {
        "zh-TW": "無資料",
        "en": "No data",
        "id": "Tidak ada data",
        "vi": "Không có dữ liệu",
        "th": "ไม่มีข้อมูล",
        "ja": "データなし",
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
    # --- Flex：服藥提醒／二次催促的藥品清單區塊 ---
    # 只出現在給用藥者的提醒卡片上；適應症等其他欄位一律不進入這裡，見 medication_flex.py。
    "flex.med.medication_list_heading": {
        "zh-TW": "本次應服藥品",
        "en": "Medications for this dose",
        "id": "Obat untuk dosis ini",
        "vi": "Thuốc cho lần uống này",
        "th": "ยาสำหรับมื้อนี้",
        "ja": "今回服用する薬",
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
}


def t(key: str, language: str | None = None) -> str:
    lang = get_request_language() if language is None else normalize_user_language(language)
    translations = _MESSAGES.get(key)
    if not translations:
        return key
    return translations.get(lang) or translations[DEFAULT_USER_LANGUAGE]


def all_sources_headings() -> frozenset[str]:
    return frozenset(t("agent.sources_heading", lang) for lang in SUPPORTED_LANGUAGES)


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
