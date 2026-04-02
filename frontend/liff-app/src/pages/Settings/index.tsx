import React, { useState, useEffect } from 'react';
import './index.css';

/* ────────── 型別定義 ────────── */
interface SettingsState {
  fontSize: 'normal' | 'large' | 'xlarge';
  highContrast: boolean;
  notifyReminder: boolean;
  notifyFamily: boolean;
}

const STORAGE_KEY = 'care-settings';

/* ────────── 預設值 ────────── */
const defaultSettings: SettingsState = {
  fontSize: 'large',       // 預設大字
  highContrast: true,       // 預設高對比
  notifyReminder: true,
  notifyFamily: true,
};

/* ────────── 字級對照 ────────── */
const fontSizeMap = {
  normal: '16px',
  large: '20px',
  xlarge: '24px',
};

const fontSizeLabelMap = {
  normal: '標準',
  large: '大',
  xlarge: '特大',
};

/* ────────── 工具函式：套用主題到 :root ────────── */
function applyTheme(settings: SettingsState) {
  const root = document.documentElement;
  root.style.setProperty('--base-font-size', fontSizeMap[settings.fontSize]);

  if (settings.highContrast) {
    root.classList.add('high-contrast');
  } else {
    root.classList.remove('high-contrast');
  }
}

/* ────────── 元件 ────────── */
const SettingsPage: React.FC = () => {
  const [settings, setSettings] = useState<SettingsState>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) return { ...defaultSettings, ...JSON.parse(raw) };
    } catch { /* ignore */ }
    return defaultSettings;
  });

  const [saved, setSaved] = useState(false);

  // 每次 settings 變動都即時套用
  useEffect(() => {
    applyTheme(settings);
  }, [settings]);

  // 儲存到 localStorage
  const handleSave = () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleFontSize = (size: SettingsState['fontSize']) => {
    setSettings((prev) => ({ ...prev, fontSize: size }));
  };

  const toggle = (key: keyof Omit<SettingsState, 'fontSize'>) => {
    setSettings((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div className="settings-page">
      <h2 className="settings-title">設定</h2>

      {/* ── 字體大小 ── */}
      <section className="settings-section">
        <h3 className="section-heading">字體大小</h3>
        <p className="section-desc">調整畫面文字大小，方便閱讀</p>
        <div className="font-size-options">
          {(['normal', 'large', 'xlarge'] as const).map((size) => (
            <button
              key={size}
              className={`font-size-btn ${settings.fontSize === size ? 'active' : ''}`}
              onClick={() => handleFontSize(size)}
              style={{ fontSize: fontSizeMap[size] }}
            >
              {fontSizeLabelMap[size]}
            </button>
          ))}
        </div>
        <div className="font-preview">
          <span>預覽文字：您好，歡迎使用 CARE 健康管家</span>
        </div>
      </section>

      {/* ── 高對比模式 ── */}
      <section className="settings-section">
        <h3 className="section-heading">高對比模式</h3>
        <p className="section-desc">加深文字與背景的對比度，讓文字更清楚</p>
        <div className="toggle-row">
          <span className="toggle-label">高對比模式</span>
          <button
            className={`toggle-switch ${settings.highContrast ? 'on' : ''}`}
            onClick={() => toggle('highContrast')}
            aria-label="切換高對比模式"
          >
            <span className="toggle-knob" />
          </button>
        </div>
      </section>

      {/* ── 通知設定 ── */}
      <section className="settings-section">
        <h3 className="section-heading">通知設定</h3>
        <p className="section-desc">管理您的提醒與通知偏好</p>

        <div className="toggle-row">
          <span className="toggle-label">用藥提醒</span>
          <button
            className={`toggle-switch ${settings.notifyReminder ? 'on' : ''}`}
            onClick={() => toggle('notifyReminder')}
            aria-label="切換用藥提醒"
          >
            <span className="toggle-knob" />
          </button>
        </div>

        <div className="toggle-row">
          <span className="toggle-label">家人健康通知</span>
          <button
            className={`toggle-switch ${settings.notifyFamily ? 'on' : ''}`}
            onClick={() => toggle('notifyFamily')}
            aria-label="切換家人健康通知"
          >
            <span className="toggle-knob" />
          </button>
        </div>
      </section>

      {/* ── 關於 ── */}
      <section className="settings-section about-section">
        <h3 className="section-heading">關於本應用</h3>
        <div className="about-info">
          <div className="about-row"><span>版本</span><strong>1.0.0</strong></div>
          <div className="about-row"><span>開發團隊</span><strong>CARE Team</strong></div>
        </div>
      </section>

      {/* ── 儲存按鈕 ── */}
      <button className="save-btn" onClick={handleSave}>
        {saved ? '已儲存！' : '儲存設定'}
      </button>
    </div>
  );
};

export default SettingsPage;

/* 匯出工具函式，讓 App 啟動時也能載入設定 */
export { applyTheme, STORAGE_KEY, defaultSettings };
export type { SettingsState };
