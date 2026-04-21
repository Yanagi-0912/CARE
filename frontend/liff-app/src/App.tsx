import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import { useEffect } from 'react'
import Homepage from './pages/Homepage'
import Header from './components/Header'
import BottomNav from './components/BottomNav'
import PersonalHealthPage from './pages/PersonalHealth'
import ConsultRecordsPage from './pages/PersonalHealth/ConsultRecords'
import SettingsPage, { applyTheme, STORAGE_KEY, defaultSettings } from './pages/Settings'
import type { SettingsState } from './pages/Settings'
import './App.css'
import { I18nProvider, useI18n, getInitialLanguage } from './i18n'

const FamilyPage = () => {
  const { t } = useI18n()
  return (
    <div className="placeholder-page">
      <h2>👥 {t('family.title')}</h2>
      <p>{t('family.desc')}</p>
    </div>
  )
}

// 新增：登入佔位頁面 (順便放一個假的 LINE 登入按鈕)
const LoginPage = () => {
  const { t } = useI18n()
  return (
    <div className="login-page">
      <h2>{t('login.title')}</h2>
      <p>{t('login.desc')}</p>
      <button className="line-login-btn">
        {t('login.button')}
      </button>
    </div>
  )
}

function AppContent() {
  /* 啟動時：讀取 SettingsPage 存的 localStorage 設定套用主題 */
  useEffect(() => {
    let settings: SettingsState = defaultSettings
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) settings = { ...defaultSettings, ...JSON.parse(raw) }
    } catch { /* ignore */ }
    applyTheme(settings)
  }, [])

  return (
    <Router>
      <div className="app-layout">
        <Header />

        <div className="main-content">
          <Routes>
            <Route path="/" element={<Homepage />} />
            <Route path="/personalhealth" element={<PersonalHealthPage />} />
            <Route path="/personalhealth/consult" element={<ConsultRecordsPage />} />
            <Route path="/family" element={<FamilyPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            {/* 新增：登入頁面的路由 */}
            <Route path="/login" element={<LoginPage />} />
          </Routes>
        </div>

        <BottomNav />
      </div>
    </Router>
  )
}

function App() {
  const initialLanguage = getInitialLanguage(STORAGE_KEY)

  return (
    <I18nProvider initialLanguage={initialLanguage}>
      <AppContent />
    </I18nProvider>
  )
}

export default App