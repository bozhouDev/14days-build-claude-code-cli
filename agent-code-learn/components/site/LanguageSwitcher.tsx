'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { defaultLocale, locales, type Locale } from '@/lib/i18n';

interface LanguageSwitcherProps {
  locale: Locale;
  /** 可选，覆盖自动读取的 pathname。默认走 usePathname()。 */
  pathnameWithoutLocale?: string;
}

function stripLocale(pathname: string, locale: Locale): string {
  if (locale === defaultLocale) return pathname || '/';
  const prefix = `/${locale}`;
  if (pathname === prefix) return '/';
  if (pathname.startsWith(`${prefix}/`)) return pathname.slice(prefix.length);
  return pathname;
}

/* Notion pill-tab 风：inactive 浅 surface 文字 + hairline 边；active = 纯黑底 + 白字。
   两段式开关，保持 pathname 不变，只改语言前缀。 */
export default function LanguageSwitcher({
  locale,
  pathnameWithoutLocale,
}: LanguageSwitcherProps) {
  const rawPath = usePathname();
  const cleanPath =
    pathnameWithoutLocale ?? stripLocale(rawPath ?? '/', locale);

  function urlFor(target: Locale): string {
    const path = cleanPath || '/';
    if (target === defaultLocale) return path;
    return `/${target}${path === '/' ? '' : path}`;
  }

  return (
    <div className="hidden md:flex items-center gap-0.5 p-0.5 rounded-[var(--ntn-rounded-md)] border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)] text-[11.5px] font-medium">
      {locales.map((l) => {
        const active = l === locale;
        return (
          <Link
            key={l}
            href={urlFor(l)}
            className={[
              'px-2 py-1 rounded-[6px] uppercase transition-colors',
              active
                ? 'bg-[color:var(--ntn-primary)] text-[color:var(--ntn-on-primary)]'
                : 'text-[color:var(--ntn-steel)] hover:text-[color:var(--ntn-ink-deep)]',
            ].join(' ')}
          >
            {l}
          </Link>
        );
      })}
    </div>
  );
}
