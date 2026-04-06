import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './index.css';
/*http://localhost:5173/personalHealth*/
interface HealthData {
    name: string;
    gender: string;
    height: string;
    weight: string;
    age: string;
    // 修改：慢性病史改為可複選
    chronicDisease: string[];
    chronicDiseaseOther: string;
    majorIllness: string;
    surgeryHistory?: string;
}

const defaultData: HealthData = {
    name: '',
    gender: '',
    height: '',
    weight: '',
    age: '',
    // 修改：慢性病史改為可複選
    chronicDisease: [],
    chronicDiseaseOther: '',
    majorIllness: '',
    surgeryHistory: '',
};

const chronicDiseaseOptions = [
    '高血壓',
    '糖尿病',
    '高血脂',
    '心臟病',
    '腎臟病',
    '氣喘',
    '慢性阻塞性肺病',
    '癌症',
    '其他',
];

const PersonalHealthPage: React.FC = () => {
    const [form, setForm] = useState<HealthData>(defaultData);
    const [otherInput, setOtherInput] = useState('');
    const [otherSaved, setOtherSaved] = useState(false);
    // 儲存成功提示狀態
    const [saveStatus, setSaveStatus] = useState<'idle' | 'success' | 'error'>('idle');
    // 儲存提示訊息
    const [saveMessage, setSaveMessage] = useState('');
    // 慢性病史下拉開關
    const [isChronicOpen, setIsChronicOpen] = useState(false);
    // 修改：性別下拉開關
    const [isGenderOpen, setIsGenderOpen] = useState(false);
    const navigate = useNavigate();

    const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
        const { name, value } = e.target;
        // 慢性病史改為可複選，這裡只處理其他欄位
        if (name === 'chronicDiseaseOther') {
            setOtherInput(value);
            setOtherSaved(false);
        } else {
            setForm((prev) => ({ ...prev, [name]: value }));
        }
    };

    // 慢性病史改為可複選（dropdown list 勾選處理）
    const handleChronicToggle = (value: string) => {
        setForm((prev) => {
            const exists = prev.chronicDisease.includes(value);
            const next = exists
                ? prev.chronicDisease.filter((item) => item !== value)
                : [...prev.chronicDisease, value];
            return { ...prev, chronicDisease: next, chronicDiseaseOther: '' };
        });
        if (value === '其他' && form.chronicDisease.includes('其他')) {
            setOtherInput('');
            setOtherSaved(false);
        }
    };

    // 修改：性別單選下拉處理
    const handleGenderSelect = (value: string) => {
        setForm((prev) => ({ ...prev, gender: value }));
        setIsGenderOpen(false);
    };

    // 修改：儲存訊息 3 秒後收回
    useEffect(() => {
        if (saveStatus === 'idle') {
            return;
        }
        const timer = window.setTimeout(() => setSaveStatus('idle'), 3000);
        return () => window.clearTimeout(timer);
    }, [saveStatus]);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        // 修改：送出前重置提示狀態
        setSaveStatus('idle');
        // 性別必填檢查，避免空值送出
        if (!form.gender) {
            setSaveMessage('請先選擇性別');
            setSaveStatus('error');
            return;
        }
        // 慢性病沒有選或是選其他但不輸入都存"無"
        // 慢性病史改為可複選，整理成陣列再轉成字串
        const hasOther = form.chronicDisease.includes('其他');
        const selected = form.chronicDisease.filter((v) => v !== '其他');
        const otherValue = otherInput.trim();
        let finalChronicList = selected;
        // 選擇其他但未輸入時，不自動塞 "無"
        if (hasOther && otherValue) {
            finalChronicList = [...selected, otherValue];
        }
        if (finalChronicList.length === 0) {
            finalChronicList = ['無'];
        }

        const finalData: HealthData = {
            ...form,
            // 慢性病史改為可複選
            chronicDisease: finalChronicList,
            // 重大傷病紀錄：如果沒填就存 "無"
            majorIllness: form.majorIllness.trim() || '無',
            // 手術史：如果沒填或不存在，就存 "無"
            surgeryHistory: (form.surgeryHistory || '').trim() || '無'
        };

        console.log("最終提交資料：", finalData);
        // TODO: 你要用真實 LINE user_id，先暫時手動填入或從 LIFF 取得
        const userId = "123456789";

        const payload = {
            name: finalData.name,
            gender: finalData.gender,
            height: Number(finalData.height),
            weight: Number(finalData.weight),
            age: Number(finalData.age),
            // 修改：慢性病史改為可複選，後端目前是字串欄位
            chronic_history: finalData.chronicDisease.join('、'),
            major_illness_history: finalData.majorIllness,
            surgery_history: finalData.surgeryHistory,
            health_consultations: {} // 先放空 JSON
        };

        const baseUrl = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

        try {
            const res = await fetch(`${baseUrl}/profiles/${userId}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const text = await res.text();
                console.error("儲存失敗:", text);
                // 儲存失敗提示狀態
                setSaveMessage('儲存失敗，請稍後再試');
                setSaveStatus('error');
                return;
            }

            const data = await res.json();
            console.log("儲存成功:", data);
            // 儲存成功提示狀態
            setSaveMessage('已成功儲存個人健康資料');
            setSaveStatus('success');
        } catch (error) {
            console.error('儲存失敗（網路或請求中斷）:', error);
            // 儲存失敗提示狀態
            setSaveMessage('網路異常，請稍後再試');
            setSaveStatus('error');
        }
    };


    const handleOtherSave = () => {
        setForm((prev) => ({ ...prev, chronicDiseaseOther: otherInput }));
        setOtherSaved(true);
    };

    // 慢性病史改為可複選
    const showOtherInput = form.chronicDisease.includes('其他');

    // 判斷是否有任一欄位有輸入
    const hasInput =
        !!form.height ||
        !!form.weight ||
        !!form.age ||
        // 慢性病史改為可複選
        form.chronicDisease.length > 0 ||
        !!form.chronicDiseaseOther ||
        !!form.majorIllness ||
        !!otherInput;

    return (
        <div className="pageContainer">
            {/* 儲存成功/失敗提示 */}
            {saveStatus === 'success' && (
                <div className="saveToast saveToastSuccess">{saveMessage || '已成功儲存個人健康資料'}</div>
            )}
            {saveStatus === 'error' && (
                <div className="saveToast saveToastError">{saveMessage || '儲存失敗，請稍後再試'}</div>
            )}
            <form id="personalHealthForm" className="formContainer" onSubmit={handleSubmit}>
                <div className="formTitle">個人健康資料</div>
                <div className="formGroup">
                    <label className="label" htmlFor="name">姓名</label>
                    <input
                        className="input"
                        type="text"
                        id="name"
                        name="name"
                        value={form.name}
                        onChange={handleChange}
                        placeholder="請輸入姓名"
                        required
                    />
                </div>
                <div className="formGroup">
                    <label className="label" htmlFor="gender">性別</label>
                    {/* 修改：性別改為自訂下拉樣式 */}
                    <div className="singleSelectWrapper">
                        <button
                            type="button"
                            className="singleSelectButton"
                            aria-haspopup="listbox"
                            aria-expanded={isGenderOpen}
                            onClick={() => setIsGenderOpen((prev) => !prev)}
                        >
                            <span className="singleSelectText">
                                {form.gender || '請選擇性別'}
                            </span>
                            <span className="singleSelectCaret" aria-hidden="true">▼</span>
                        </button>
                        {isGenderOpen && (
                            <div className="singleSelectMenu" role="listbox">
                                {['男', '女'].map((opt) => (
                                    <button
                                        key={opt}
                                        type="button"
                                        className={`singleSelectItem ${form.gender === opt ? 'isSelected' : ''}`}
                                        onClick={() => handleGenderSelect(opt)}
                                    >
                                        {opt}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
                <div className="formGroup">

                    <label className="label" htmlFor="height">身高 (cm)</label>
                    <input
                        className="input"
                        type="number"
                        id="height"
                        name="height"
                        value={form.height}
                        onChange={handleChange}
                        placeholder="請輸入身高"
                        min="0"
                        required
                    />
                </div>
                <div className="formGroup">
                    <label className="label" htmlFor="weight">體重 (kg)</label>
                    <input
                        className="input"
                        type="number"
                        id="weight"
                        name="weight"
                        value={form.weight}
                        onChange={handleChange}
                        placeholder="請輸入體重"
                        min="0"
                        required
                    />
                </div>
                <div className="formGroup">
                    <label className="label" htmlFor="age">年齡</label>
                    <input
                        className="input"
                        type="number"
                        id="age"
                        name="age"
                        value={form.age}
                        onChange={handleChange}
                        placeholder="請輸入年齡"
                        min="0"
                        required
                    />
                </div>
                <div className="formGroup">
                    <label className="label">慢性病史</label>
                    {/* 用自訂下拉與勾勾標記取代藍色選取背景 */}
                    <div className="multiSelectWrapper" style={{ marginBottom: showOtherInput ? 8 : 0 }}>
                        <button
                            type="button"
                            className="multiSelectButton"
                            aria-haspopup="listbox"
                            aria-expanded={isChronicOpen}
                            onClick={() => setIsChronicOpen((prev) => !prev)}
                        >
                            <span className="multiSelectText">
                                {form.chronicDisease.length > 0
                                    ? form.chronicDisease.join('、')
                                    : '請選擇慢性病史'}
                            </span>
                            <span className="multiSelectCaret" aria-hidden="true">▼</span>
                        </button>
                        {isChronicOpen && (
                            <div className="multiSelectMenu" role="listbox" aria-multiselectable="true">
                                {chronicDiseaseOptions.map(opt => {
                                    const checked = form.chronicDisease.includes(opt);
                                    return (
                                        <button
                                            key={opt}
                                            type="button"
                                            className={`multiSelectItem ${checked ? 'isSelected' : ''}`}
                                            onClick={() => handleChronicToggle(opt)}
                                        >
                                            <span className="multiSelectCheck" aria-hidden="true">
                                                {checked ? '✓' : ''}
                                            </span>
                                            <span>{opt}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                    {showOtherInput && (
                        <div className="otherInputRow">
                            <input
                                className="input"
                                type="text"
                                name="chronicDiseaseOther"
                                value={otherInput}
                                onChange={handleChange}
                                placeholder="請輸入其他慢性病"
                            />
                            <button
                                type="button"
                                aria-label="儲存其他慢性病"
                                onClick={handleOtherSave}
                                disabled={!otherInput.trim()}
                            >
                                {/*勾勾圖案的svg*/}
                                <svg width="24px" height="24px" viewBox="0 0 24 24" role="img" xmlns="http://www.w3.org/2000/svg" aria-labelledby="okIconTitle" stroke="#4a90e2" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none" color="#4a90e2">
                                    <title id="okIconTitle">Ok</title>
                                    <polyline points="4 13 9 18 20 7" />
                                </svg>
                            </button>
                            {otherSaved && <span style={{ color: '#000000', fontSize: 14 }}>已儲存</span>}
                        </div>
                    )}
                </div>
                <div className="formGroup">
                    <label className="label" htmlFor="majorIllness">重大傷病紀錄</label>
                    <textarea
                        className="input longInput"
                        id="majorIllness"
                        name="majorIllness"
                        value={form.majorIllness}
                        onChange={handleChange}
                        placeholder="請輸入重大傷病紀錄 (如無則不需填寫)"
                        rows={2}
                    />
                </div>
                <div className="formGroup">
                    <label className="label" htmlFor="surgeryHistory">開刀紀錄</label>
                    <textarea
                        className="input longInput"
                        id="surgeryHistory"
                        name="surgeryHistory"
                        value={form.surgeryHistory}
                        onChange={handleChange}
                        placeholder="請輸入開刀紀錄 (如無則不需填寫)"
                        rows={2}
                    />
                </div>
            </form>
            <div className="actionRow">
                {hasInput && (
                    <button className="button" type="submit" form="personalHealthForm">儲存紀錄</button>
                )}
                <button onClick={() => navigate('/personalhealth/consult')} className="button consultButton">
                    查看諮詢紀錄
                </button>
            </div>

        </div>
    );
};

export default PersonalHealthPage;