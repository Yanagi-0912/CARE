import { useNavigate } from 'react-router-dom';
import './index.css';
import { useI18n } from '../../i18n';

function Homepage() {
  const navigate = useNavigate();
  const { t } = useI18n();

  return (
    <main className="home-container">
      <section className="hero-section">
        <h2>{t('home.greetingTitle')}</h2>
        <p>{t('home.greetingDesc')}</p>
      </section>

      <section className="feature-grid">
        <button
          className="feature-card health-card"
          onClick={() => navigate('/personalhealth')}
        >
          <div className="card-icon">🏥</div>
          <h3>{t('home.personalHealth')}</h3>
          <p>{t('home.personalHealthDesc')}</p>
        </button>

        <button
          className="feature-card family-card"
          onClick={() => navigate('/family')}
        >
          <div className="card-icon">👨‍👩‍👧‍👦</div>
          <h3>{t('home.family')}</h3>
          <p>{t('home.familyDesc')}</p>
        </button>

        <button
          className="feature-card settings-card"
          onClick={() => navigate('/settings')}
        >
          <div className="card-icon">⚙️</div>
          <h3>{t('home.settings')}</h3>
          <p>{t('home.settingsDesc')}</p>
        </button>
      </section>
    </main>
  );
}

export default Homepage;