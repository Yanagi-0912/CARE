import { useNavigate } from 'react-router-dom';
import './index.css';

function Homepage() {
  const navigate = useNavigate();

  return (
    <main className="home-container">
      <section className="hero-section">
        <h2>早安，今天想了解什麼？</h2>
        <p>您的專屬健康與預約管理小幫手</p>
      </section>

      <section className="feature-grid">
        <button
          className="feature-card health-card"
          onClick={() => navigate('/personalhealth')}
        >
          <div className="card-icon">🏥</div>
          <h3>個人健康</h3>
          <p>健康紀錄與醫院預約</p>
        </button>

        <button
          className="feature-card family-card"
          onClick={() => navigate('/family')}
        >
          <div className="card-icon">👨‍👩‍👧‍👦</div>
          <h3>家庭介面</h3>
          <p>管理長輩與家人狀況</p>
        </button>

        <button
          className="feature-card settings-card"
          onClick={() => navigate('/settings')}
        >
          <div className="card-icon">⚙️</div>
          <h3>設定頁面</h3>
          <p>系統偏好與通知管理</p>
        </button>
      </section>
    </main>
  );
}

export default Homepage;