import { createContext, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { fallbackLanguage, messages, type SupportedLanguage } from './messages';

type TranslateFn = (key: string) => string;

interface I18nContextValue {
  language: SupportedLanguage;
  setLanguage: (lang: SupportedLanguage) => void;
  t: TranslateFn;
}

const I18nContext = createContext<I18nContextValue | undefined>(undefined);

export function isSupportedLanguage(value: string): value is SupportedLanguage {
  return value in messages;
}

export function getInitialLanguage(storageKey = 'care-settings'): SupportedLanguage {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return fallbackLanguage;
    const parsed = JSON.parse(raw);
    if (isSupportedLanguage(parsed.language)) {
      return parsed.language;
    }
    return fallbackLanguage;
  } catch {
    return fallbackLanguage;
  }
}

export function I18nProvider({
  children,
  initialLanguage = fallbackLanguage,
}: {
  children: ReactNode;
  initialLanguage?: SupportedLanguage;
}) {
  const [language, setLanguage] = useState<SupportedLanguage>(initialLanguage);

  const t = useMemo<TranslateFn>(() => {
    return (key: string) =>
      messages[language][key] ?? messages[fallbackLanguage][key] ?? key;
  }, [language]);

  const value = useMemo(
    () => ({ language, setLanguage, t }),
    [language, setLanguage, t],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error('useI18n must be used within I18nProvider');
  }
  return context;
}
