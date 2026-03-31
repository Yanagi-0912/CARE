import { useNavigate, useLocation } from 'react-router-dom';
import './index.css';

function BottomNav() {
  const navigate = useNavigate();
  const location = useLocation();

  // 判斷目前網址是否與按鈕對應，來決定要不要加上 active class
  const isActive = (path: string) => location.pathname === path ? 'active' : '';

  return (
    <nav className="bottom-nav">
      <button 
        className={`nav-item ${isActive('/')}`} 
        onClick={() => navigate('/')}
      >
        <span className="icon">🏠</span>
        <span className="label">首頁</span>
      </button>
      <button 
        className={`nav-item ${isActive('/health')}`} 
        onClick={() => navigate('/health')}
      >
        <span className="icon">🏥</span>
        <span className="label">健康</span>
      </button>
      <button 
        className={`nav-item ${isActive('/family')}`} 
        onClick={() => navigate('/family')}
      >
        <span className="icon">👥</span>
        <span className="label">家庭</span>
      </button>
      <button 
        className={`nav-item ${isActive('/settings')}`} 
        onClick={() => navigate('/settings')}
      >
        <span className="icon">⚙️</span>
        <span className="label">設定</span>
      </button>
    </nav>
  );
}

export default BottomNav;