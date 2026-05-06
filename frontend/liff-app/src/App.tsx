import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useEffect } from 'react';
import Header from './components/Header';
import BottomNav from './components/BottomNav';
import Sidebar from './components/Sidebar';
import Home from './pages/Home';
import PersonalHealth from './pages/PersonalHealth';
import Family from './pages/Family';
import ConsultRecordsPage from './pages/PersonalHealth/ConsultRecords';
import { I18nProvider, getInitialLanguage } from './i18n';
import SettingsPage, { applyTheme, STORAGE_KEY, defaultSettings } from './pages/Settings';
import type { SettingsState } from './pages/Settings';
import './App.css';
import Login from './pages/Loginpage';

// 1. 新增 ProtectedRoute 元件
function ProtectedRoute({ children }: { children: JSX.Element }) {
  const token = localStorage.getItem('CARE_AUTH_TOKEN');
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function AppContent() {
  // 2. 取得當前路徑，用來判斷是否要顯示導覽列
  const location = useLocation();
  const isLoginPage = location.pathname === '/login';

  useEffect(() => {
    let settings: SettingsState = defaultSettings;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) settings = { ...defaultSettings, ...JSON.parse(raw) };
    } catch {
      // ignore
    }
    applyTheme(settings);
  }, []);

  return (
    <div className="app-layout">
      {/* 3. 如果不是登入頁，才顯示 Header */}
      {!isLoginPage && <Header />}
      
      <div className="main-wrapper">
        {/* 3. 如果不是登入頁，才顯示 Sidebar */}
        {!isLoginPage && <Sidebar />}
        
        <main className="content-area">
          <Routes>
            <Route path="/login" element={<Login />} />
            
            {/* 4. 套用 ProtectedRoute */}
            <Route path="/" element={<ProtectedRoute><Home /></ProtectedRoute>} />
            <Route path="/personalhealth" element={<ProtectedRoute><PersonalHealth /></ProtectedRoute>} />
            <Route path="/personalhealth/consult" element={<ProtectedRoute><ConsultRecordsPage /></ProtectedRoute>} />
            <Route path="/family" element={<ProtectedRoute><Family /></ProtectedRoute>} />
            <Route path="/settings" element={<ProtectedRoute><SettingsPage /></ProtectedRoute>} />
          </Routes>
        </main>
      </div>
      
      {/* 3. 如果不是登入頁，才顯示 BottomNav */}
      {!isLoginPage && <BottomNav />}
    </div>
  );
}

function App() {
  const initialLanguage = getInitialLanguage(STORAGE_KEY);
  return (
    <I18nProvider initialLanguage={initialLanguage}>
      {/* 5. 將 Router 移到這裡，讓內部的 AppContent 可以使用 useLocation */}
      <Router>
        <AppContent />
      </Router>
    </I18nProvider>
  );
}

export default App;