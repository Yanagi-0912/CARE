import { fireEvent, render, screen } from '@testing-library/react';

import { I18nProvider, getInitialLanguage } from '../i18n';
import SettingsPage from '../pages/Settings';

function renderSettings(initialLanguage = 'zh-TW' as const) {
  return render(
    <I18nProvider initialLanguage={initialLanguage}>
      <SettingsPage />
    </I18nProvider>,
  );
}

describe('設定頁語言行為', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('切換語言後，應同步更新 localStorage 與畫面語言', () => {
    renderSettings();

    const select = screen.getByLabelText('顯示語言') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'en' } });
//使用者改語言 → 有沒有存進 localStorage
    const saved = JSON.parse(localStorage.getItem('care-settings') || '{}');
    expect(saved.language).toBe('en');
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
  });
//UI 有沒有真的變英文
  it('重新掛載後，應保留先前選擇的語言', () => {
    localStorage.setItem(
      'care-settings',
      JSON.stringify({
        fontSize: 'large',
        language: 'en',
        highContrast: true,
        notifyReminder: true,
        notifyFamily: true,
      }),
    );

    renderSettings(getInitialLanguage('care-settings'));

    const select = screen.getByLabelText('Display Language') as HTMLSelectElement;
    expect(select.value).toBe('en');
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
  });
});
