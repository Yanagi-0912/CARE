import React, { useState, useEffect } from 'react';
import './index.css';
import { useI18n } from '../../i18n';
import type { SupportedLanguage } from '../../i18n/messages';

/* ────────── 型別定義 ────────── */
interface SettingsState {
  fontSize: 'normal' | 'large' | 'xlarge';
  language: SupportedLanguage;
  highContrast: boolean;
  notifyReminder: boolean;
  notifyFamily: boolean;
}

const STORAGE_KEY = 'care-settings';

/* ────────── 預設值 ────────── */
const defaultSettings: SettingsState = {
  fontSize: 'large',       // 預設大字
  language: 'zh-TW',
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

const languageOptions: Array<{ value: SettingsState['language']; label: string }> = [
  { value: 'zh-TW', label: '繁體中文' },
  { value: 'en', label: 'English' },
  { value: 'id', label: 'Bahasa Indonesia' },
  { value: 'vi', label: 'Tiếng Việt' },
  { value: 'th', label: 'ไทย' },
  { value: 'ja', label: '日本語' },
];

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
  const { t, setLanguage, language } = useI18n();
  const fontSizeLabelMap = {
    normal: t('settings.fontSizeNormal'),
    large: t('settings.fontSizeLarge'),
    xlarge: t('settings.fontSizeXLarge'),
  };
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

  // 每次設定變動都同步到 localStorage，避免語言切換後重載回舊值
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  }, [settings]);

  // 以 i18n 全域語言為準，確保下拉顯示與頁面語言一致
  useEffect(() => {
    setSettings((prev) => (
      prev.language === language ? prev : { ...prev, language }
    ));
  }, [language]);

  // 顯示儲存成功提示
  const handleSave = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleFontSize = (size: SettingsState['fontSize']) => {
    setSettings((prev) => ({ ...prev, fontSize: size }));
  };

  const handleLanguage = (language: SettingsState['language']) => {
    setSettings((prev) => ({ ...prev, language }));
    setLanguage(language);
  };

  const toggle = (key: keyof Omit<SettingsState, 'fontSize'>) => {
    setSettings((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div className="settings-page">
      <h2 className="settings-title">{t('settings.title')}</h2>

      {/* ── 字體大小 ── */}
      <section className="settings-section">
        <h3 className="section-heading">{t('settings.fontSizeTitle')}</h3>
        <p className="section-desc">{t('settings.fontSizeDesc')}</p>
        <div className="font-size-options">
          {(['normal', 'large', 'xlarge'] as const).map((size) => (
            <button
              key={size}
              className={`font-size-btn font-size-btn-${size} ${settings.fontSize === size ? 'active' : ''}`}
              onClick={() => handleFontSize(size)}
            >
              {fontSizeLabelMap[size]}
            </button>
          ))}
        </div>
        <div className="font-preview">
          <span>{t('settings.preview')}</span>
        </div>
      </section>

      {/* ── 語言設定 ── */}
      <section className="settings-section">
        <h3 className="section-heading">{t('settings.languageTitle')}</h3>
        <p className="section-desc">{t('settings.languageDesc')}</p>
        <div className="select-row">
          <label htmlFor="language-select" className="toggle-label">{t('settings.displayLanguage')}</label>
          <select
            id="language-select"
            className="settings-select"
            value={settings.language}
            onChange={(e) => handleLanguage(e.target.value as SettingsState['language'])}
          >
            {languageOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </section>

      {/* ── 高對比模式 ── */}
      <section className="settings-section">
        <h3 className="section-heading">{t('settings.highContrastTitle')}</h3>
        <p className="section-desc">{t('settings.highContrastDesc')}</p>
        <div className="toggle-row">
          <span className="toggle-label">{t('settings.highContrastToggle')}</span>
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
        <h3 className="section-heading">{t('settings.notificationsTitle')}</h3>
        <p className="section-desc">{t('settings.notificationsDesc')}</p>

        <div className="toggle-row">
          <span className="toggle-label">{t('settings.medicationReminder')}</span>
          <button
            className={`toggle-switch ${settings.notifyReminder ? 'on' : ''}`}
            onClick={() => toggle('notifyReminder')}
            aria-label="切換用藥提醒"
          >
            <span className="toggle-knob" />
          </button>
        </div>

        <div className="toggle-row">
          <span className="toggle-label">{t('settings.familyAlert')}</span>
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
        <h3 className="section-heading">{t('settings.aboutTitle')}</h3>
        <div className="about-info">
          <div className="about-row"><span>{t('settings.version')}</span><strong>1.0.0</strong></div>
          <div className="about-row"><span>{t('settings.team')}</span><strong>CARE Team</strong></div>
        </div>
      </section>

      {/* ── 儲存按鈕 ── */}
      <button className="save-btn" onClick={handleSave}>
        {saved ? t('settings.saved') : t('settings.save')}
      </button>
    </div>
  );
};

export default SettingsPage;

/* 匯出工具函式，讓 App 啟動時也能載入設定 */
export { applyTheme, STORAGE_KEY, defaultSettings };
export type { SettingsState };
