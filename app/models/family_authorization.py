"""家庭授權的角色、資料分類、權限矩陣與欄位分類登記表。

這個模組是家庭授權的**唯一真相**。判定要用到的每一張表都在這裡，
`app/services/family/family_authorization_service.py` 只負責查表，不自行推導；
router 與 service 一律 SHALL NOT 自行判斷「這份資料算哪一級」。

四張表各自回答一個問題，刻意不互相推導：

- `PERMISSIONS`：某個角色對某一級資料能不能讀／寫
- `CLASSIFICATION_OF`：某個資源整體屬於哪一級
- `FIELD_CLASSIFICATION`：跨使用者輸出時，每一個欄位屬於哪一級（**fail-closed**）
- `NOTIFICATION_POLICY`：某種推播該送給哪些角色

`NOTIFICATION_POLICY` 與 `PERMISSIONS` 分開，是因為兩者回答的是不同問題。
「他可以隨時去看我的健康資料」與「我出事時要通知他」是兩種不同的信任，
一個人可能只給其中一種；綁在一起等於逼使用者為了讓某人收到警報而交出
日常的查看權。
"""

from typing import Literal, Optional

# 四種家庭角色。`OWNER` 由「操作者即資料擁有者」推導而來，SHALL NOT 儲存、
# SHALL NOT 指派——它是「這份資料是誰的」這個事實，不是一種可授予的權限。
FamilyRole = Literal["OWNER", "GUARDIAN", "CAREGIVER", "MEMBER"]

# 可指派給族譜成員的角色。`OWNER` 不在其中（見上）。
ASSIGNABLE_FAMILY_ROLES: frozenset[str] = frozenset({"GUARDIAN", "CAREGIVER", "MEMBER"})

# 族譜成員項目缺 `family_role` 欄位時的解讀。
#
# 這個預設值只作用於**授權**：確保沒有人因為缺欄位而取得超額權限。它 SHALL NOT
# 用來判斷「擁有者是否已完成引導式指派」——那裡要求欄位實際存在，見
# FamilyAuthorizationService.is_role_assignment_complete。兩者在授權上等價、
# 在指派上不等價，這個區分由欄位的「有無」承載，不需要額外欄位。
DEFAULT_FAMILY_ROLE: FamilyRole = "MEMBER"

# 三級資料分類。
#
# - GENERAL：用藥設定（藥品名稱、時段、頻率、提醒規則）、掛號提醒，以及顯示名稱與頭像
# - SENSITIVE：健康狀況（年齡、性別、身高、體重、病史），以及適應症
# - PRIVATE：與 LINE 健康機器人的對話摘要與原始逐句對話
DataClassification = Literal["GENERAL", "SENSITIVE", "PRIVATE"]

Action = Literal["READ", "WRITE"]

# 授權矩陣。判定 SHALL 完全由這張表決定，端點 SHALL NOT 自行加寬或加嚴。
#
# `OWNER` 這一列就是「一個人對自己資料的權限」——因為角色解析在
# `operator == target` 時直接回 `OWNER`，矩陣不需要額外的「是否為自己資料」
# 布林輸入，那件事已經被吸收進角色解析裡。
#
# WRITE 不蘊含 READ，READ 也不蘊含 WRITE：兩個動作各自查表。矩陣目前沒有
# 「可寫不可讀」的格子，但把蘊含關係寫進程式碼會讓這張表不再是唯一真相——
# 日後要加一個「可提交但不可回看」的分類時，那條隱含規則會擋住它，而且擋在
# 一個沒人記得的地方。
PERMISSIONS: dict[FamilyRole, dict[DataClassification, frozenset[Action]]] = {
    "OWNER": {
        "GENERAL": frozenset({"READ", "WRITE"}),
        "SENSITIVE": frozenset({"READ", "WRITE"}),
        "PRIVATE": frozenset({"READ", "WRITE"}),
    },
    "GUARDIAN": {
        "GENERAL": frozenset({"READ", "WRITE"}),
        "SENSITIVE": frozenset({"READ", "WRITE"}),
        "PRIVATE": frozenset({"READ"}),
    },
    "CAREGIVER": {
        "GENERAL": frozenset({"READ", "WRITE"}),
        "SENSITIVE": frozenset({"READ"}),
        "PRIVATE": frozenset(),
    },
    "MEMBER": {
        "GENERAL": frozenset({"READ"}),
        "SENSITIVE": frozenset(),
        "PRIVATE": frozenset(),
    },
}

# 受保護的資源。名稱是授權判定的參數，與端點路徑無關——同一個資源可能由
# 多支端點回傳，它們必須套用同一級分類。
ResourceName = Literal[
    "medication_reminder",
    "medication",
    "appointment_reminder",
    "health_profile",
    "consultation_summary",
    "consultation_raw",
]

# 資源整體的分類。
#
# 對話摘要與原始逐句對話都是 PRIVATE：摘要是原始對話的濃縮，把它降一級等於
# 讓同一份內容從側門走出去。
CLASSIFICATION_OF: dict[ResourceName, DataClassification] = {
    "medication_reminder": "GENERAL",
    "medication": "GENERAL",
    "appointment_reminder": "GENERAL",
    "health_profile": "SENSITIVE",
    "consultation_summary": "PRIVATE",
    "consultation_raw": "PRIVATE",
}

# 欄位分類登記表。跨使用者輸出的資源，**每一個**欄位都要在這裡登記。
#
# 未登記的欄位 SHALL NOT 跨使用者輸出，SHALL NOT 落回資源的預設分類——這是
# fail-closed，而方向是重點。若未登記者落回預設分類，任何人日後為 Medication
# 新增一個欄位（例如另一種病情描述）就會在沒有錯誤、沒有提示的情況下對權限
# 最低的成員公開：開發者不會想到要來改這張表，而漏掉沒有任何回饋訊號。
# fail-closed 之後，漏掉的後果是「家人畫面上那個欄位不見了」——會被回報、
# 不會外洩。守門測試（tests/unit/models/test_family_authorization.py）會在
# 模型新增欄位而未登記時失敗，那是這條規則唯一的早期警報。
#
# 本表只作用於**跨使用者**輸出。操作者讀自己的資料不經遮蔽，否則新增一個欄位
# 會連本人都看不到自己的資料。
FIELD_CLASSIFICATION: dict[tuple[ResourceName, str], DataClassification] = {
    # ── 用藥提醒規則 ──────────────────────────────────────────────
    ("medication_reminder", "id"): "GENERAL",
    ("medication_reminder", "creator_user_id"): "GENERAL",
    ("medication_reminder", "user_id"): "GENERAL",
    ("medication_reminder", "slot_type"): "GENERAL",
    ("medication_reminder", "scheduled_time"): "GENERAL",
    ("medication_reminder", "start_date"): "GENERAL",
    ("medication_reminder", "end_date"): "GENERAL",
    ("medication_reminder", "enabled"): "GENERAL",
    ("medication_reminder", "medication_ids"): "GENERAL",
    ("medication_reminder", "entries"): "GENERAL",
    ("medication_reminder", "timeout_anchor_time"): "GENERAL",
    ("medication_reminder", "medications"): "GENERAL",
    ("medication_reminder", "created_at"): "GENERAL",
    ("medication_reminder", "updated_at"): "GENERAL",
    ("medication_reminder", "enabled_at"): "GENERAL",
    # ── 掛號提醒 ──────────────────────────────────────────────────
    # 整份資源與用藥同級，全部 GENERAL——包括科別。有 GENERAL 讀取權的家屬在
    # LIFF 內本來就該看得到完整的門診資訊。科別的敏感性是**推播通道**的問題
    # （訊息會躺在聊天室列表裡、同一支手機的其他人看得到），那條邊界由
    # appointment_flex 的參數形狀守住——builder 根本不收科別、醫師與看診號——
    # 而不是由這張表。
    ("appointment_reminder", "id"): "GENERAL",
    ("appointment_reminder", "user_id"): "GENERAL",
    ("appointment_reminder", "creator_user_id"): "GENERAL",
    ("appointment_reminder", "appointment_at"): "GENERAL",
    ("appointment_reminder", "facility_id"): "GENERAL",
    ("appointment_reminder", "hospital_name"): "GENERAL",
    ("appointment_reminder", "hospital_address"): "GENERAL",
    ("appointment_reminder", "hospital_phone"): "GENERAL",
    ("appointment_reminder", "department"): "GENERAL",
    ("appointment_reminder", "doctor_name"): "GENERAL",
    ("appointment_reminder", "serial_number"): "GENERAL",
    ("appointment_reminder", "note"): "GENERAL",
    ("appointment_reminder", "status"): "GENERAL",
    ("appointment_reminder", "departed_at"): "GENERAL",
    ("appointment_reminder", "departed_by_user_id"): "GENERAL",
    ("appointment_reminder", "attended_at"): "GENERAL",
    ("appointment_reminder", "attended_by_user_id"): "GENERAL",
    ("appointment_reminder", "cancelled_at"): "GENERAL",
    ("appointment_reminder", "cancelled_by_user_id"): "GENERAL",
    ("appointment_reminder", "enabled"): "GENERAL",
    ("appointment_reminder", "notify_at"): "GENERAL",
    ("appointment_reminder", "created_at"): "GENERAL",
    ("appointment_reminder", "updated_at"): "GENERAL",
    # ── 藥品 ──────────────────────────────────────────────────────
    ("medication", "id"): "GENERAL",
    ("medication", "user_id"): "GENERAL",
    ("medication", "created_by_user_id"): "GENERAL",
    ("medication", "name"): "GENERAL",
    ("medication", "generic_name"): "GENERAL",
    ("medication", "license_number"): "GENERAL",
    ("medication", "shape"): "GENERAL",
    ("medication", "color"): "GENERAL",
    ("medication", "score_line"): "GENERAL",
    ("medication", "mark_one"): "GENERAL",
    ("medication", "mark_two"): "GENERAL",
    ("medication", "size"): "GENERAL",
    ("medication", "thumbnail_url"): "GENERAL",
    ("medication", "unit_content"): "GENERAL",
    ("medication", "total_quantity"): "GENERAL",
    ("medication", "usage_raw"): "GENERAL",
    # 調劑日期單獨不揭露病情（與 start_date 同級）；它與 institution 併在
    # 一起才構成看診紀錄，而那個組合的分級由 institution 決定。
    ("medication", "dispensed_date"): "GENERAL",
    ("medication", "draft_id"): "GENERAL",
    ("medication", "frequency_code"): "GENERAL",
    ("medication", "source"): "GENERAL",
    ("medication", "start_date"): "GENERAL",
    ("medication", "end_date"): "GENERAL",
    ("medication", "enabled"): "GENERAL",
    ("medication", "created_at"): "GENERAL",
    ("medication", "updated_at"): "GENERAL",
    # 三個適應症欄位同屬 SENSITIVE：它們回答的都是「這個人為什麼吃這個藥」。
    # 留在 GENERAL 的話，MEMBER 就能繞過他對 SENSITIVE 的無存取權，從藥品
    # 說明反推長輩的慢性病——那正是資料分類要防止的事。
    # 調劑機構是健康狀況，不是用藥設定：「常去腫瘤科」「上個月去了身心科」
    # 揭露的病情遠多於藥名。MEMBER 對 GENERAL 有讀取權、對 SENSITIVE 沒有，
    # 放錯級別會讓族譜裡的一般成員看得到長輩去了哪些科別。
    ("medication", "institution"): "SENSITIVE",
    ("medication", "indication"): "SENSITIVE",
    ("medication", "spc_indication"): "SENSITIVE",
    ("medication", "spc_indication_summary"): "SENSITIVE",
    # ── 健康檔案 ──────────────────────────────────────────────────
    # 顯示名稱與頭像是 GENERAL：族譜清單靠它們回答「這是誰」。歸入更高的
    # 分類，權限最低的成員會看到一份無名氏清單，連要向誰求助都不知道。
    #
    # 注意這是**讀取**面的分類。GENERAL 的寫入權 SHALL NOT 因此涵蓋這兩個
    # 欄位——見 spec「代理寫入的範圍」，它們要改得經由獨立的
    # profile-management 授權，代理寫入路徑一律不動它們。
    ("health_profile", "line_id"): "GENERAL",
    ("health_profile", "name"): "GENERAL",
    ("health_profile", "picture_url"): "GENERAL",
    ("health_profile", "age"): "SENSITIVE",
    ("health_profile", "gender"): "SENSITIVE",
    ("health_profile", "height"): "SENSITIVE",
    ("health_profile", "weight"): "SENSITIVE",
    ("health_profile", "chronic_diseases"): "SENSITIVE",
    ("health_profile", "chronic_custom"): "SENSITIVE",
    ("health_profile", "major_illness_history"): "SENSITIVE",
    ("health_profile", "surgery_history"): "SENSITIVE",
}

# 刻意不登記、因此永遠不跨使用者輸出的欄位。
#
# 這張表存在的唯一理由是讓「漏登記」與「刻意不給」在程式碼裡分得出來。守門
# 測試要求跨使用者輸出模型的每一個欄位，不是登記在 FIELD_CLASSIFICATION，
# 就是列在這裡——新增欄位若兩邊都沒有，測試就會失敗。少了這張表，守門測試
# 只能放寬成「允許有未登記的欄位」，那等於沒有守門。
DELIBERATELY_UNEXPOSED_FIELDS: frozenset[tuple[ResourceName, str]] = frozenset(
    {
        # 系統角色與介面偏好都不屬於家庭授權的管轄範圍：家人沒有理由知道你是
        # 不是管理員、或你把字級調到多大。
        ("health_profile", "role"),
        ("health_profile", "settings"),
        # 健康檔案的建檔與更新時間對家人沒有意義，且會洩漏使用頻率。
        ("health_profile", "created_at"),
        ("health_profile", "updated_at"),
    }
)

# 代理寫入 SHALL NOT 觸碰的欄位。
#
# `display_name`／`picture_url` 分類為 GENERAL 是為了讓族譜清單顯示得出「這是
# 誰」，那是讀取面的決定。若讓 GENERAL 的寫入權一併涵蓋它們，一位 CAREGIVER
# 就能改掉長輩在所有家人畫面上的名字與頭像。分類回答「誰看得到」，不回答
# 「誰改得動」。
#
# `role`／`settings` 則是完全不同的軸：系統角色與介面偏好都不屬於家庭授權的
# 管轄範圍，代理寫入沒有任何理由碰它們。
PROXY_WRITE_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {"name", "display_name", "picture_url", "role", "settings", "line_id"}
)

# 推播種類，包含:高風險藥物、加入非處方藥、緊急事件偵測、掛號提醒、用藥逾時未確認
NotificationKind = Literal[
    "high_risk_drug_alert",
    "otc_medication_added",
    "emergency_detected",
    "appointment_reminder",
    "medication_missed",
]

# 通知政策。**與 PERMISSIONS 分開宣告，兩者的變更互不牽動。**
#
# 當事人本人恆為收件人，不經此表。`MEMBER` 不在任何一種推播的集合內——
# 他 SHALL NOT 因為具備 GENERAL 讀取權而自動收到警報。
#
# CAREGIVER 納入高風險藥物通報，正是「通知與讀取權分離」最好的例子：他在
# 資料上只有 SENSITIVE 讀取權、沒有 PRIVATE，但現實中往往是唯一在場的人。
# 排除他，通報就送給了三小時後才會看手機的子女，而不是此刻站在長輩旁邊的
# 那個人。**他該收到通報，但不該因此看得到對話紀錄**——收到通知 SHALL NOT
# 改變任何資料存取權。
NOTIFICATION_POLICY: dict[NotificationKind, frozenset[FamilyRole]] = {
    "high_risk_drug_alert": frozenset({"GUARDIAN", "CAREGIVER"}),
    # 長輩自行加入非處方藥。收件人與高風險通報相同，理由也相同——能收到完整
    # 訊息（含用途與成分重複說明，皆為 SENSITIVE）的就是這兩個角色。
    #
    # MEMBER 刻意不納入，理由不只是權限：他依授權矩陣看不到 SENSITIVE，能收到
    # 的只有「某人新增了某某藥」，沒有用途也沒有風險說明——那是一則沒有行動
    # 價值的訊息。持續發送低價值訊息會讓收件人靜音整個帳號，連帶淹沒真正需要
    # 注意的警報。2026-08 曾因空提醒卡打爆 LINE 月額度，推播成本是實際約束。
    "otc_medication_added": frozenset({"GUARDIAN", "CAREGIVER"}),
    # 對話中偵測到需要立即處置的狀況（意識不清、大量出血、自傷或自盡表達…）。
    "emergency_detected": frozenset({"GUARDIAN", "CAREGIVER"}),
    # 掛號提醒：T-1h、T+0 兩則推播與 T+30 家屬警報**共用這一份收件人名單**，
    # 不會有兩套（已拍板：三則推播與一則警報的收件人是同一群人）。
    #
    # 收件角色必須是「按得下卡片按鈕的人」：卡片上有「我已出發／我已到診」，
    # 代按需要 GENERAL 寫入權，而 MEMBER 只有讀——把 MEMBER 納入等於送他一張
    # 按下去必定 403 的卡片。GENERAL 的寫入者恰好就是 GUARDIAN 與 CAREGIVER
    # （OWNER 是本人，不經此表）。
    #
    # 兩種模式都照這一列篩選（見下方 STRICT_NOTIFICATION_KINDS）：掛號的寫入在影子
    # 模式下同樣是嚴格判定，推播若在影子模式下擴及全員，MEMBER 就會收到按了必定
    # 403 的卡片。
    "appointment_reminder": frozenset({"GUARDIAN", "CAREGIVER"}),
    # 用藥 T+30 逾時未確認的家屬通報，以及停機期間錯過時段的彙整通知。
    #
    # 本變更前只送給**規則的建立者**：家屬替長輩設的提醒家屬收得到，長輩自己
    # 設的則推回長輩本人，家屬一則都收不到。
    #
    # 刻意**不**列入 STRICT_NOTIFICATION_KINDS，理由與掛號提醒相反：
    # - 掛號卡片上有要寫入權的按鈕，MEMBER 收到會按了必定 403；這張卡片沒有任何
    #   按鈕，只是告知。
    # - 影子模式下送族譜全員，是以前「只送建立者」的超集——建立者本來就在族譜裡
    #   （能替長輩設提醒就是家人），今天收得到的人一個都不會漏掉。嚴格篩選的話，
    #   還沒指派角色的家庭（目前的預設狀態）會連建立者都收不到。
    # 強制之後照這一列篩選，只送給能管理用藥設定的 GUARDIAN 與 CAREGIVER。
    "medication_missed": frozenset({"GUARDIAN", "CAREGIVER"}),
}

# 影子模式下**仍然**依 NOTIFICATION_POLICY 篩選收件人的推播種類。
#
# 影子模式承諾「行為與導入前完全相同」，所以其他種類在影子模式下送族譜全員。但
# 導入 RBAC 之後才有的推播沒有「導入前」可言——與 `authorize` 的
# `has_legacy_equivalent=False` 是同一個道理。掛號提醒整個功能都是新的，它的寫入
# 一律嚴格判定（已拍板），收件人必須跟著嚴格，卡片上的按鈕才按得下去。
STRICT_NOTIFICATION_KINDS: frozenset[NotificationKind] = frozenset(
    {"appointment_reminder"}
)

# 每位資料擁有者各自持有的遷移狀態。強制以**擁有者**為邊界逐一啟用，
# 不是單一全域切換——全域切換的那一刻，所有尚未指派角色的擁有者，其家人會
# 同時失去功能，而尚未指派正是預設狀態。
MigrationState = Literal["shadow", "enforced"]

DEFAULT_MIGRATION_STATE: MigrationState = "shadow"


def is_allowed(
    role: Optional[FamilyRole],
    classification: DataClassification,
    action: Action,
    permissions: Optional[dict[FamilyRole, dict[DataClassification, frozenset[Action]]]] = None,
) -> bool:
    """查表判定某個角色對某一級資料能否執行某個動作。

    `role` 為 None 代表操作者不在目標擁有者的族譜內——不是家人就沒有任何權限，
    直接回 False，不落入矩陣。

    `permissions` 讓測試餵入合成的矩陣（例如一格「可寫不可讀」）來驗證這裡
    沒有任何蘊含邏輯。正式矩陣目前沒有那樣的格子，所以不靠資料本身就驗不出
    「WRITE 不蘊含 READ」——但那條規則要在加入新分類之前就守住，不能等到
    有人踩到才發現。以參數注入而非 monkey patch，是專案既有的測試慣例。
    """
    if role is None:
        return False
    table = PERMISSIONS if permissions is None else permissions
    return action in table[role][classification]


def field_classification(
    resource: ResourceName, field: str
) -> Optional[DataClassification]:
    """回傳欄位的分類；未登記時回 None。

    呼叫端 SHALL 把 None 當作「不可跨使用者輸出」，SHALL NOT 代換成資源的
    預設分類——那會讓漏登記的新欄位靜默外洩（見 FIELD_CLASSIFICATION 的說明）。
    """
    return FIELD_CLASSIFICATION.get((resource, field))


def notification_recipient_roles(kind: NotificationKind) -> frozenset[FamilyRole]:
    """回傳某種推播的合格收件角色。

    刻意不落回 `PERMISSIONS`：通知政策與資料存取授權是兩套獨立的表，任何
    「查不到就用讀取權代替」的降級都會讓分離失效。

    政策表未涵蓋的種類直接拋 KeyError：`NotificationKind` 是 Literal，型別
    檢查已經擋得住拼錯，因此執行期查不到只可能是「新增了推播種類卻忘了加進
    政策表」——那該在開發期就爆，而不是上線後安靜地不通知任何人。
    """
    return NOTIFICATION_POLICY[kind]


__all__ = [
    "FamilyRole",
    "ASSIGNABLE_FAMILY_ROLES",
    "DEFAULT_FAMILY_ROLE",
    "DataClassification",
    "Action",
    "PERMISSIONS",
    "ResourceName",
    "CLASSIFICATION_OF",
    "FIELD_CLASSIFICATION",
    "DELIBERATELY_UNEXPOSED_FIELDS",
    "PROXY_WRITE_FORBIDDEN_FIELDS",
    "NotificationKind",
    "NOTIFICATION_POLICY",
    "STRICT_NOTIFICATION_KINDS",
    "MigrationState",
    "DEFAULT_MIGRATION_STATE",
    "is_allowed",
    "field_classification",
    "notification_recipient_roles",
]
