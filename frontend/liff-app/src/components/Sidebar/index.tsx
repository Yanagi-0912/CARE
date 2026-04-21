import { useNavigate, useLocation } from 'react-router-dom';
import './index.css';

function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();

  const isActive = (path: string) => (location.pathname === path ? 'active' : '');

  return (
    <aside className="sidebar">
      <nav className="sidebar-nav">
        <button className={`side-item ${isActive('/')}`} onClick={() => navigate('/')}>
          <span className="icon">🏠</span>首頁
        </button>
        <button className={`side-item ${isActive('/personalhealth')}`} onClick={() => navigate('/PersonalHealth')}>
          <span className="icon">🏥</span>個人健康
        </button>
        <button className={`side-item ${isActive('/family')}`} onClick={() => navigate('/family')}>
          <span className="icon">👥</span>家庭介面
        </button>
        <button className={`side-item ${isActive('/settings')}`} onClick={() => navigate('/Settings')}>
          <span className="icon">⚙️</span>系統設定
        </button>
      </nav>
    </aside>
  );
}

export default Sidebar;