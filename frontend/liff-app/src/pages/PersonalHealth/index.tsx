import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { upsertPersonalHealthProfile, getPersonalHealthProfile } from '../../api/profileApi';

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

interface DecodedIdToken {
    name?: string;
    picture?: string;
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
    // 顯示使用者名稱與頭像
    const [userName, setUserName] = useState<string>('');
    const [userAvatar, setUserAvatar] = useState<string>('');
    const navigate = useNavigate();

    const readDecodedIdToken = (): DecodedIdToken | null => {
        const liffId = (import.meta.env.VITE_LIFF_ID ?? '').trim();
        if (liffId) {
            const storedKey = `LIFF_STORE:${liffId}:decodedIDToken`;
            const storedValue = localStorage.getItem(storedKey);
            if (storedValue) {
                try {
                    return JSON.parse(storedValue) as DecodedIdToken;
                } catch (error) {
                    console.warn('解析 decodedIDToken 失敗:', error);
                }
            }
        }

        const fallbackKey = Object.keys(localStorage).find((key) => key.endsWith(':decodedIDToken'));
        if (!fallbackKey) {
            return null;
        }

        const fallbackValue = localStorage.getItem(fallbackKey);
        if (!fallbackValue) {
            return null;
        }

        try {
            return JSON.parse(fallbackValue) as DecodedIdToken;
        } catch (error) {
            console.warn('解析 fallback decodedIDToken 失敗:', error);
            return null;
        }
    };

    useEffect(() => {
        const decodedIdToken = readDecodedIdToken();
        const displayName = (decodedIdToken?.name || localStorage.getItem('CARE_LINE_DISPLAY_NAME') || '').trim();
        const avatarUrl = (decodedIdToken?.picture || '').trim();

        if (displayName) {
            setUserName(displayName);
            setForm((prev) => ({ ...prev, name: prev.name || displayName }));
        }

        if (avatarUrl) {
            setUserAvatar(avatarUrl);
        }

        // 讀取使用者資料，預填表單
        const loadUserData = async () => {
            const userId = (localStorage.getItem('CARE_LINE_USER_ID') || '').trim();
            if (!userId) {
                console.log('未找到使用者ID，跳過加載資料');
                return;
            }

            try {
                //呼叫api取得user資料
                const data = await getPersonalHealthProfile(userId);
                console.log('已加載使用者資料:', data);
                if (data) {
                    setForm((prev) => ({
                        ...prev,
                        name: data.name || prev.name,  // 資料庫優先，沒有才用LINE 名稱
                        gender: data.gender || '',
                        height: data.height?.toString() || '',
                        weight: data.weight?.toString() || '',
                        age: data.age?.toString() || '',
                        chronicDisease: data.chronic_history ? data.chronic_history.split('、').filter(Boolean) : [],
                        chronicDiseaseOther: '',
                        majorIllness: data.major_illness_history || '',
                        surgeryHistory: data.surgery_history || '',
                    }));
                    // 若資料庫有 name，也更新顯示用的 userName
                    if (data.name) {
                        setUserName(data.name);
                    }
                }
            } catch (error) {
                console.warn('載入使用者資料失敗:', error);
                // 不阻斷頁面使用
            }
        };

        loadUserData();
    }, []);


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
        // 不再寫死Id，而是從 LIFF 取得 userId 作為 API 識別用
        const userId = (localStorage.getItem('CARE_LINE_USER_ID') || '').trim();
        if (!userId) {
            setSaveMessage('找不到 LINE 使用者資訊，請先重新登入');
            setSaveStatus('error');
            return;
        }
        const profileName = userName || (readDecodedIdToken()?.name || '').trim();
        console.log("LIFF User ID:", userId);
        console.log("LIFF User Name:", profileName);
        const payload = {
            name: finalData.name,
            gender: finalData.gender,
            height: Number(finalData.height),
            weight: Number(finalData.weight),
            age: Number(finalData.age),
            // 修改：慢性病史改為可複選，後端目前是字串欄位
            chronic_history: finalData.chronicDisease.join('、'),
            major_illness_history: finalData.majorIllness,
            surgery_history: finalData.surgeryHistory || '無',
            health_consultations: {} // 先放空 JSON
        };


        try {
            const data = await upsertPersonalHealthProfile(userId, payload);
            console.log('儲存成功:', data);
            setSaveMessage('已成功儲存個人健康資料');
            setSaveStatus('success');
        } catch (error) {
            console.error('儲存失敗（網路或請求中斷）:', error);
            setSaveMessage(error instanceof Error ? error.message : '網路異常，請稍後再試');
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

    const isLoggedIn = Boolean(
        (localStorage.getItem('CARE_AUTH_TOKEN') || '').trim() &&
        (localStorage.getItem('CARE_LINE_USER_ID') || '').trim()
    );

    return (
        <div className="pageContainer">
            <section className="profileBanner">
                <div className="profileAvatarWrap">
                    {userAvatar ? (
                        <img
                            className="profileAvatar"
                            src={userAvatar}
                            alt={userName ? `${userName} 的頭像` : '使用者頭像'}
                        />
                    ) : (
                        <div className="profileAvatar profileAvatarFallback" aria-hidden="true">
                            {userName ? userName.charAt(0) : 'U'}
                        </div>
                    )}
                </div>
                <div className="profileBannerText">
                    <div className="profileBannerLabel">{isLoggedIn ? '已登入' : '您尚未登入!'}</div>
                    <div className="formTitle profileBannerTitle">{userName ? `${userName} 的健康資料` : '個人健康資料'}</div>
                </div>
            </section>

            {/* 儲存成功/失敗提示 */}
            {saveStatus === 'success' && (
                <div className="saveToast saveToastSuccess">{saveMessage || '已成功儲存個人健康資料'}</div>
            )}
            {saveStatus === 'error' && (
                <div className="saveToast saveToastError">{saveMessage || '儲存失敗，請稍後再試'}</div>
            )}
            <form id="personalHealthForm" className="formContainer" onSubmit={handleSubmit}>
                <div className="formGroup">
                    <label className="label" htmlFor="name">姓名</label>
                    <input
                        className="input"
                        type="text"
                        id="name"
                        name="name"
                        value={form.name}
                        onChange={handleChange}
                        /*若有登入就顯示user名字*/
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