import React, { useState, useEffect } from 'react';
import './index.css';

interface ConsultRecord {
    id: string;
    userMessage: string;
    aiReply: string;
    summary: string;
    timestamp: number;
}

const LOCAL_KEY = 'consult_records';
const DAY_MS = 24 * 60 * 60 * 1000;

function saveRecords(records: ConsultRecord[]) {
    try {
        localStorage.setItem(LOCAL_KEY, JSON.stringify(records));
    }
    catch {
        console.error('Failed to save consult records');
    }
}

function loadRecords(): ConsultRecord[] {
    try {
        const raw = localStorage.getItem(LOCAL_KEY);
        if (!raw) return [];
        const arr = JSON.parse(raw);
        return Array.isArray(arr) ? arr : [];
    }
    catch {
        console.error('Failed to load consult records');
        return [];
    }
}

const ConsultRecordsPage: React.FC = () => {
    const [records, setRecords] = useState<ConsultRecord[]>([]);
    const mockSummaries = [
        '近三日持續出現夜間咳嗽，建議先觀察並記錄發作時間。',
        '飯後胃部不適，先少量多餐並避免刺激性食物。',
        '運動後膝蓋酸痛，先冰敷並降低負重活動。',
    ];

    // 載入紀錄，僅保留 7 天內
    useEffect(() => {
        const now = Date.now();
        const all = loadRecords();
        const filtered = all.filter(r => now - r.timestamp < 7 * DAY_MS);
        setRecords(filtered);
        if (filtered.length !== all.length) saveRecords(filtered);
    }, []);

    // 下載 json
    const handleDownload = () => {
        const data = JSON.stringify(records, null, 2);
        const blob = new Blob([data], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `consult_records_${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        // 1 秒後釋放 URL 物件
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    };

    return (
        <div className="consult-page">
            <header className="consult-header">
                <h2>健康諮詢紀錄</h2>
                <p>保存 7 天內的對話，並以 AI 摘要整理重點。</p>
            </header>
            <section className="consult-card">
                <div className="panel">
                    <div className="panel-top">
                        <div className="panel-time">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
                        <div className="panel-notch">
                            <div className="panel-dot" />
                            <div className="panel-speaker" />
                            <div className="panel-dot" />
                        </div>
                        <div className="panel-status">
                            {/*wifi icon*/}
                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="iconOnPhone">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M8.288 15.038a5.25 5.25 0 0 1 7.424 0M5.106 11.856c3.807-3.808 9.98-3.808 13.788 0M1.924 8.674c5.565-5.565 14.587-5.565 20.152 0M12.53 18.22l-.53.53-.53-.53a.75.75 0 0 1 1.06 0Z" />
                            </svg>
                            {/*battery icon*/}
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="iconOnPhone">
                                <path fillRule="evenodd" d="M3.75 6.75a3 3 0 0 0-3 3v6a3 3 0 0 0 3 3h15a3 3 0 0 0 3-3v-.037c.856-.174 1.5-.93 1.5-1.838v-2.25c0-.907-.644-1.664-1.5-1.837V9.75a3 3 0 0 0-3-3h-15Zm15 1.5a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1-1.5 1.5h-15a1.5 1.5 0 0 1-1.5-1.5v-6a1.5 1.5 0 0 1 1.5-1.5h15ZM4.5 9.75a.75.75 0 0 0-.75.75V15c0 .414.336.75.75.75H18a.75.75 0 0 0 .75-.75v-4.5a.75.75 0 0 0-.75-.75H4.5Z" clipRule="evenodd" />
                            </svg>
                        </div>

                    </div>
                    <div className="panel-list">
                        {mockSummaries.map((text, index) => (
                            <div key={`summary_${index}`} className="panel-item">
                                <div className="panel-badge">AI</div>
                                <p>{text}</p>
                            </div>
                        ))}
                    </div>
                </div>
                <div className="form-actions">
                    <button onClick={handleDownload} className="btn ghost">下載所有紀錄（JSON）</button>
                </div>
            </section>

            <section className="records">
                {records.length === 0 && <div className="empty">目前沒有紀錄</div>}
                {records.map(r => (
                    <div key={r.id} className="record">
                        <div className="record-meta">{new Date(r.timestamp).toLocaleString()}</div>
                        <div className="record-row"><span>你</span>{r.userMessage}</div>
                        <div className="record-row"><span>AI</span>{r.aiReply}</div>
                        <div className="record-summary"><span>AI 摘要</span>{r.summary}</div>
                    </div>
                ))}
            </section>
        </div>
    );
};

export default ConsultRecordsPage;
