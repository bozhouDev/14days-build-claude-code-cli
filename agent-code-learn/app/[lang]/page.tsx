import { defaultLocale, locales, type Locale } from '@/lib/i18n';
import TopNav from '@/components/site/TopNav';
import Footer from '@/components/site/Footer';
import LandingPage from '@/components/site/LandingPage';
import { getLessons } from '@/lib/lessons';

interface Params {
  lang: string;
}

export default async function RootPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { lang } = await params;
  const locale = (locales as string[]).includes(lang) ? (lang as Locale) : defaultLocale;
  const lessons = getLessons(locale);

  return (
    <div className="min-h-screen bg-[color:var(--ntn-canvas)]">
      <TopNav locale={locale} pathnameWithoutLocale="/" />
      <LandingPage locale={locale} lessons={lessons} />
      <Footer locale={locale} />
    </div>
  );
}

export function generateStaticParams() {
  return locales.map((lang) => ({ lang }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}) {
  const { lang } = await params;
  const locale = (locales as string[]).includes(lang) ? (lang as Locale) : defaultLocale;

  if (locale === 'en') {
    return {
      title: 'buildcc.dev · Build a Code Agent CLI in 14 days',
      description:
        'Build a Claude Code style Agent CLI from scratch: Python, tools, permissions, sessions, and harness engineering.',
    };
  }

  return {
    title: 'buildcc.dev · 14 天从零手搓 Code Agent CLI',
    description:
      '从零实现一个 Claude Code 风格的 Code Agent CLI：Python、工具调用、权限、会话记忆和 Agent Harness。',
  };
}
