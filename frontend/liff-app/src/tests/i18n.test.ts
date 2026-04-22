import { getInitialLanguage, isSupportedLanguage } from '../i18n';

describe('i18n', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('returns language from localStorage when supported', () => {
    localStorage.setItem('care-settings', JSON.stringify({ language: 'vi' }));
    expect(getInitialLanguage('care-settings')).toBe('vi');
  });

  it('falls back to zh-TW when language is unsupported', () => {
    localStorage.setItem('care-settings', JSON.stringify({ language: 'fr' }));
    expect(getInitialLanguage('care-settings')).toBe('zh-TW');
  });

  it('falls back to zh-TW when storage is malformed', () => {
    localStorage.setItem('care-settings', '{broken-json');
    expect(getInitialLanguage('care-settings')).toBe('zh-TW');
  });

  it('checks supported language values', () => {
    expect(isSupportedLanguage('en')).toBe(true);
    expect(isSupportedLanguage('zh-TW')).toBe(true);
    expect(isSupportedLanguage('fr')).toBe(false);
  });
});
