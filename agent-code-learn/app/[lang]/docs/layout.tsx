import type { ReactNode } from 'react';
import TopNav from '@/components/site/TopNav';
import Footer from '@/components/site/Footer';
import { defaultLocale, locales, type Locale } from '@/lib/i18n';

interface Params {
  lang: string;
}

/* docs layout 只挂顶部导航 + 容器 + 底部 footer；三栏内容由 page.tsx 渲染。 */
export default async function DocsLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<Params>;
}) {
  const { lang } = await params;
  const locale = (locales as string[]).includes(lang) ? (lang as Locale) : defaultLocale;
  return (
    <div className="min-h-screen flex flex-col bg-[color:var(--ntn-canvas)]">
      <TopNav locale={locale} />
      <main className="flex-1">{children}</main>
      <Footer locale={locale} />
    </div>
  );
}
