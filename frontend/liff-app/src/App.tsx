import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { useEffect } from 'react';
import Header from './components/Header';
import BottomNav from './components/BottomNav';
import Sidebar from './components/Sidebar';
import Home from './pages/Home';
import PersonalHealth from './pages/PersonalHealth';
import Family from './pages/Family';
import ConsultRecordsPage from './pages/PersonalHealth/ConsultRecords';
import { I18nProvider, useI18n, getInitialLanguage } from './i18n';
import SettingsPage, { applyTheme, STORAGE_KEY, defaultSettings } from './pages/Settings';
import type { SettingsState } from './pages/Settings';
import './App.css';

/* 佔位組件：後續開發可直接替換檔案 */
const Login = () => {
  const { t } = useI18n();
  return (
    <div className="login-page">
      <h2>{t('login.title')}</h2>
      <p>{t('login.desc')}</p>
      <button className="line-login-btn">{t('login.button')}</button>
    </div>
  );
};

function AppContent() {
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
    <Router>
      <div className="app-layout">
        <Header />
        <div className="main-wrapper">
          <Sidebar />
          <main className="content-area">
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/personalhealth" element={<PersonalHealth />} />
              <Route path="/personalhealth/consult" element={<ConsultRecordsPage />} />
              <Route path="/family" element={<Family />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/login" element={<Login />} />
            </Routes>
          </main>
        </div>
        <BottomNav />
      </div>
    </Router>
  );
}

function App() {
  const initialLanguage = getInitialLanguage(STORAGE_KEY);

  return (
    <I18nProvider initialLanguage={initialLanguage}>
      <AppContent />
    </I18nProvider>
  );
}

export default App;
