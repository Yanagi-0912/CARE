import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Header from './components/Header';
import BottomNav from './components/BottomNav';
import Sidebar from './components/Sidebar';
import Home from './pages/Home';
import PersonalHealth from './pages/PersonalHealth';
import Settings from './pages/Settings';

/* 佔位組件：後續開發可直接替換檔案 */
const Family = () => <div className="placeholder">👥 家庭頁面（開發中）</div>;
const Login = () => <div className="placeholder">🔑 登入頁面（開發中）</div>;

function App() {
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
              <Route path="/family" element={<Family />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/login" element={<Login />} />
            </Routes>
          </main>
        </div>
        <BottomNav />
      </div>
    </Router>
  );
}

export default App;