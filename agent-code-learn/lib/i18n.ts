import type { I18nConfig } from 'fumadocs-core/i18n';

export type Locale = 'zh' | 'en';

export const locales: Locale[] = ['zh', 'en'];

export const defaultLocale: Locale = 'zh';

export const i18n: I18nConfig<Locale> = {
  defaultLanguage: defaultLocale,
  languages: locales,
  hideLocale: 'default-locale',
};

export const localeMeta: Record<Locale, { label: string; native: string }> = {
  zh: { label: 'Chinese', native: '中文' },
  en: { label: 'English', native: 'English' },
};

export function localeFromPathname(pathname: string | null | undefined): Locale {
  const firstSegment = pathname?.split('/').filter(Boolean)[0];
  return firstSegment === 'en' ? 'en' : defaultLocale;
}
