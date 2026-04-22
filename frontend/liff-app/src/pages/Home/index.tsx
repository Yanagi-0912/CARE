import { useNavigate } from 'react-router-dom';
import './index.css';

/** 首頁主入口：提供四個主要功能導航 */
const Home = () => {
  const navigate = useNavigate();

  const features = [
    { title: '個人健康', icon: '🏥', path: '/personalhealth', desc: '紀錄與預約' },
    { title: '家庭介面', icon: '👥', path: '/family', desc: '管理家人狀況' },
    { title: '設定頁面', icon: '⚙️', path: '/settings', desc: '系統偏好設定' },
    { title: '帳號登入', icon: '🔑', path: '/login', desc: '切換 LINE 帳號' },
  ];

  return (
    <div className="home-grid">
      <header className="home-hero">
        <h1>CARE 健康管家</h1>
        <p>點選下方卡片開始管理您的健康</p>
      </header>

      <section className="card-container">
        {features.map((f) => (
          <button key={f.path} className="feature-card" onClick={() => navigate(f.path)}>
            <span className="card-icon">{f.icon}</span>
            <div className="card-info">
              <h3>{f.title}</h3>
              <p>{f.desc}</p>
            </div>
          </button>
        ))}
      </section>
    </div>
  );
};

export default Home;