import { getInitialLanguage, isSupportedLanguage } from '../i18n';
//先讀 localStorage
//parse JSON
//檢查 language 是否支援
//失敗一律 fallback zh-TW
describe('多語系初始化邏輯', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('當 localStorage 是支援語言時，應回傳該語言', () => {
    localStorage.setItem('care-settings', JSON.stringify({ language: 'vi' }));
    expect(getInitialLanguage('care-settings')).toBe('vi');
  });
//localStorage 裡有值，但語言不支援
  it('當語言不支援時，應回退到 zh-TW', () => {
    localStorage.setItem('care-settings', JSON.stringify({ language: 'fr' }));
    expect(getInitialLanguage('care-settings')).toBe('zh-TW');
  });
//localStorage 裡有值，但語言不支援
  it('當 localStorage 格式損壞時，應回退到 zh-TW', () => {
    localStorage.setItem('care-settings', '{broken-json');
    expect(getInitialLanguage('care-settings')).toBe('zh-TW');
  });
//ocalStorage 壞掉，JSON 解析失敗
  it('應正確判斷語言是否為支援清單', () => {
    expect(isSupportedLanguage('en')).toBe(true);
    expect(isSupportedLanguage('zh-TW')).toBe(true);
    expect(isSupportedLanguage('fr')).toBe(false);
  });
});
