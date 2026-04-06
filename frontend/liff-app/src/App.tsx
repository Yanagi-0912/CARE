import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import { useEffect } from 'react'
import Homepage from './pages/Homepage'
import Header from './components/Header'
import BottomNav from './components/BottomNav'
import PersonalHealthPage from './pages/PersonalHealth'
import ConsultRecordsPage from './pages/PersonalHealth/ConsultRecords'
import SettingsPage, { applyTheme, STORAGE_KEY, defaultSettings } from './pages/Settings'
import type { SettingsState } from './pages/Settings'

// 原本的三個佔位頁面 (此處保留 Family，而 Settings 已抽出)

// 原本的三個佔位頁面
const HealthPage = () => (
  <div style={{ padding: '24px' }}>
    <h2>🏥 個人健康</h2>
    <p>這裡將顯示您的健康紀錄與醫院預約功能。</p>
  </div>
)

const FamilyPage = () => (
  <div style={{ padding: '24px' }}>
    <h2>👥 家庭介面</h2>
    <p>這裡將管理長輩與家人的健康狀況。</p>
  </div>
)

// 新增：登入佔位頁面 (順便放一個假的 LINE 登入按鈕)
const LoginPage = () => (
  <div style={{ padding: '48px 24px', textAlign: 'center' }}>
    <h2>登入 CARE</h2>
    <p style={{ color: '#6b7280', marginBottom: '24px' }}>請登入以查看您的專屬健康資訊</p>
    <button style={{
      backgroundColor: '#06C755', /* LINE 官方綠色 */
      color: 'white',
      border: 'none',
      padding: '12px 24px',
      borderRadius: '8px',
      fontSize: '1rem',
      fontWeight: 'bold',
      cursor: 'pointer'
    }}>
      使用 LINE 帳號登入
    </button>
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
            {/* 新增：登入頁面的路由 */}
            <Route path="/login" element={<LoginPage />} />
          </Routes>
        </div>

        <BottomNav />
      </div>
    </Router>
  )
}

export default App