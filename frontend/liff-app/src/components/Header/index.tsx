import { useNavigate } from 'react-router-dom';
import './index.css';
import { useI18n } from '../../i18n';

function Header() {
  const navigate = useNavigate();
  const { t } = useI18n();

  return (
    <header className="app-header">
      <div className="header-container">
        {/* 左側 Logo (加上游標指標與點擊回首頁功能) */}
        <h1 
          className="header-logo clickable"
          onClick={() => navigate('/')}
        >
          CARE
        </h1>
        
        {/* 中間搜尋框 */}
        <div className="search-box">
          <input 
            type="text" 
            placeholder={t('header.searchPlaceholder')}
            className="search-input"
          />
          <button className="search-btn" aria-label={t('header.searchAriaLabel')}>🔍</button>
        </div>

        {/* 右側按鈕：點擊前往 /login */}
        <nav className="header-nav">
          <button 
            className="login-btn" 
            onClick={() => navigate('/login')}
          >
            {t('header.login')}
          </button>
        </nav>
      </div>
    </header>
  );
}

export default Header;