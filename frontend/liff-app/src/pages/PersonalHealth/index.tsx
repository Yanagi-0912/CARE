import React, { useState } from 'react';
import './index.css';
/*http://localhost:5173/personalHealth*/
interface HealthData {
    name: string;
    gender: string;
    height: string;
    weight: string;
    age: string;
    chronicDisease: string;
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
    chronicDisease: '',
    chronicDiseaseOther: '',
    majorIllness: '',
    surgeryHistory: '',
};

const chronicDiseaseOptions = [
    '無',
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
    const [saved, setSaved] = useState<HealthData | null>(null);
    const [otherInput, setOtherInput] = useState('');
    const [otherSaved, setOtherSaved] = useState(false);

    const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
        const { name, value } = e.target;
        if (name === 'chronicDisease') {
            setForm((prev) => ({ ...prev, chronicDisease: value, chronicDiseaseOther: '' }));
            setOtherInput('');
            setOtherSaved(false);
        } else if (name === 'chronicDiseaseOther') {
            setOtherInput(value);
            setOtherSaved(false);
        } else {
            setForm((prev) => ({ ...prev, [name]: value }));
        }
    };

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        let chronicDisease = form.chronicDisease;
        let chronicDiseaseOther = form.chronicDiseaseOther;
        if (chronicDisease === '') {
            chronicDisease = '無';
            chronicDiseaseOther = '';
        }

        else if (chronicDisease === '其他') {
            /*有填寫就保存填寫的內容，若是空的就存"無" */
            chronicDiseaseOther = (otherSaved && otherInput.trim()) ? otherInput : '無';
        }
        setSaved({ ...form, chronicDisease, chronicDiseaseOther });
    };

    const handleOtherSave = () => {
        setForm((prev) => ({ ...prev, chronicDiseaseOther: otherInput }));
        setOtherSaved(true);
    };

    const showOtherInput = form.chronicDisease === '其他';

    // 判斷是否有任一欄位有輸入
    const hasInput =
        !!form.height ||
        !!form.weight ||
        !!form.age ||
        !!form.chronicDisease ||
        !!form.chronicDiseaseOther ||
        !!form.majorIllness ||
        !!otherInput;

    return (
        <div className="pageContainer">
            <form className="formContainer" onSubmit={handleSubmit}>
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
                    <select
                        className="input"
                        id="gender"
                        name="gender"
                        value={form.gender}
                        onChange={handleChange}
                        required
                    >
                        <option value="">請選擇性別</option>
                        <option value="男">男</option>
                        <option value="女">女</option>
                    </select>
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
                    <label className="label" htmlFor="chronicDisease">慢性病史</label>
                    <select
                        className="input"
                        id="chronicDisease"
                        name="chronicDisease"
                        value={form.chronicDisease}
                        onChange={handleChange}
                        style={{ marginBottom: showOtherInput ? 8 : 0 }}
                    >
                        <option value="" disabled>請選擇慢性病史</option>
                        {chronicDiseaseOptions.map(opt => (
                            <option key={opt} value={opt}>{opt}</option>
                        ))}
                    </select>
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
                {hasInput && (
                    <button className="button" type="submit">儲存紀錄</button>
                )}
            </form>
            <div className="consultRecord">
                <h2>諮詢紀錄(還沒做)
                </h2>
            </div>
            {saved && (
                <div className="result">
                    <div>姓名：{saved.name}</div>
                    <div>性別：{saved.gender}</div>
                    <div>身高：{saved.height} cm</div>
                    <div>體重：{saved.weight} kg</div>
                    <div>年齡：{saved.age}</div>
                    {/*選擇其他慢性病但是不輸入就存"無"*/}
                    <div>
                        慢性病史：
                        {saved.chronicDisease}
                    </div>
                    <div>重大傷病紀錄：{saved.majorIllness || '無'}</div>
                </div>
            )}
        </div>
    );
};

export default PersonalHealthPage;
