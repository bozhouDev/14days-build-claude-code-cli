import { RootProvider } from 'fumadocs-ui/provider/next';
import type { ReactNode } from 'react';
import { defaultLocale, locales, type Locale } from '@/lib/i18n';

interface Params {
  lang: string;
}

export function generateStaticParams() {
  return locales.map(lang => ({ lang }));
}

export default async function LangLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<Params>;
}) {
  const { lang } = await params;
  const locale = (locales as string[]).includes(lang) ? (lang as Locale) : defaultLocale;

  return (
    <RootProvider
      theme={{ enabled: false }}
      i18n={{
        locale,
        locales: [
          { name: '中文', locale: 'zh' },
          { name: 'English', locale: 'en' },
        ],
      }}
    >
      {children}
    </RootProvider>
  );
}
