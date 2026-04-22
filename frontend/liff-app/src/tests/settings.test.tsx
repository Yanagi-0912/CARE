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

describe('Settings language behavior', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('updates localStorage and UI language when selection changes', () => {
    renderSettings();

    const select = screen.getByLabelText('顯示語言') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'en' } });

    const saved = JSON.parse(localStorage.getItem('care-settings') || '{}');
    expect(saved.language).toBe('en');
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
  });

  it('keeps selected language after re-mount', () => {
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
