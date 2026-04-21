import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import { useEffect } from 'react'
import Homepage from './pages/Homepage'
import Header from './components/Header'
import BottomNav from './components/BottomNav'
import PersonalHealthPage from './pages/PersonalHealth'
import ConsultRecordsPage from './pages/PersonalHealth/ConsultRecords'
import LoginPage from './pages/Loginpage'
import SettingsPage, { applyTheme, STORAGE_KEY, defaultSettings } from './pages/Settings'
import type { SettingsState } from './pages/Settings'

// 原本的三個佔位頁面 (此處保留 Family，而 Settings 已抽出)

const FamilyPage = () => (
  <div style={{ padding: '24px' }}>
    <h2>👥 家庭介面</h2>
    <p>這裡將管理長輩與家人的健康狀況。</p>
  </div>
)

function App() {
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

        <div className="main-content" style={{ paddingBottom: '80px' }}>
          <Routes>
            <Route path="/" element={<Homepage />} />
            <Route path="/personalhealth" element={<PersonalHealthPage />} />
            <Route path="/personalhealth/consult" element={<ConsultRecordsPage />} />
            <Route path="/family" element={<FamilyPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/login" element={<LoginPage />} />
          </Routes>
        </div>

        <BottomNav />
      </div>
    </Router>
  )
}

export default App