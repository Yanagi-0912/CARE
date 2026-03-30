import { useNavigate } from 'react-router-dom';
import './index.css';

function Header() {
  const navigate = useNavigate();

  return (
    <header className="app-header">
      <div className="header-container">
        {/* 左側 Logo (加上游標指標與點擊回首頁功能) */}
        <h1 
          className="header-logo" 
          onClick={() => navigate('/')} 
          style={{ cursor: 'pointer' }}
        >
          CARE
        </h1>
        
        {/* 中間搜尋框 */}
        <div className="search-box">
          <input 
            type="text" 
            placeholder="搜尋附近醫院或診所..." 
            className="search-input"
          />
          <button className="search-btn" aria-label="搜尋">🔍</button>
        </div>

        {/* 右側按鈕：點擊前往 /login */}
        <nav className="header-nav">
          <button 
            className="login-btn" 
            onClick={() => navigate('/login')}
          >
            登入
          </button>
        </nav>
      </div>
    </header>
  );
}

export default Header;