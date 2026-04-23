import { useNavigate, useLocation } from 'react-router-dom';
import './index.css';
import { useI18n } from '../../i18n';

function BottomNav() {
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useI18n();

  // 判斷目前網址是否與按鈕對應，來決定要不要加上 active class
  const isActive = (path: string) => location.pathname === path ? 'active' : '';

  return (
    <nav className="bottom-nav">
      <button
        className={`nav-item ${isActive('/')}`}
        onClick={() => navigate('/')}
      >
        <span className="icon">🏠</span>
        <span className="label">{t('nav.home')}</span>
      </button>
      <button
        className={`nav-item ${isActive('/PersonalHealth')}`}
        onClick={() => navigate('/PersonalHealth')}
      >
        <span className="icon">🏥</span>
        <span className="label">{t('nav.health')}</span>
      </button>
      <button
        className={`nav-item ${isActive('/Family')}`}
        onClick={() => navigate('/Family')}
      >
        <span className="icon">👥</span>
        <span className="label">{t('nav.family')}</span>
      </button>
      <button
        className={`nav-item ${isActive('/Settings')}`}
        onClick={() => navigate('/Settings')}
      >
        <span className="icon">⚙️</span>
        <span className="label">{t('nav.settings')}</span>
      </button>
    </nav>
  );
}

export default BottomNav;