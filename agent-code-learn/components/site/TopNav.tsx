import Link from 'next/link';
import { Github } from 'lucide-react';
import { COPY } from '@/lib/copy';
import { localizedHref } from '@/lib/href';
import type { Locale } from '@/lib/i18n';
import LanguageSwitcher from './LanguageSwitcher';
import ThemeToggle from './ThemeToggle';

interface TopNavProps {
  locale: Locale;
  /** 可选；不传时 LanguageSwitcher 会自动读 usePathname()。 */
  pathnameWithoutLocale?: string;
}

/* 仓库地址放在导航里给读者一个出口；写死是因为整站只有这一份教程项目，没有动态化必要。 */
const REPO_URL = 'https://github.com/anthropics/claude-code';

/* 文档站只需要最小化顶栏：左侧 BuildCC logo + 站点名（点击回根路径，会被 / page 重定向到 Day 1），
   右侧只保留 GitHub 入口 + 语言切换 + 主题切换。中间章节导航由左侧 LessonSidebar 承担，不再重复。 */
export default function TopNav({ locale, pathnameWithoutLocale }: TopNavProps) {
  const copy = COPY[locale].chrome;

  return (
    <header
      className="
        sticky top-0 z-40 w-full
        bg-[color:var(--ntn-canvas)]/75
        backdrop-blur-xl backdrop-saturate-150
        border-b border-[color:var(--ntn-hairline)]
        supports-[backdrop-filter]:bg-[color:var(--ntn-canvas)]/65
      "
    >
      <div className="max-w-[1400px] mx-auto px-6 h-16 flex items-center justify-between gap-6">
        <Link
          href={localizedHref('/', locale)}
          className="
            group flex items-center gap-2.5 shrink-0 rounded-[var(--ntn-rounded-md)]
            -mx-1 px-1 py-1 transition-colors
            hover:bg-[color:var(--ntn-surface)]
          "
        >
          {/* BuildCC mark：双层 C 形框架 + prompt 光标，暗色方块保证 24px 仍可辨认。 */}
          <span
            className="
              w-7 h-7 rounded-[var(--ntn-rounded-md)]
              bg-[#0F0F12]
              flex items-center justify-center
              font-bold text-[15px] leading-none
              shadow-[inset_0_1px_0_rgba(255,255,255,0.12),0_1px_2px_rgba(0,0,0,0.06)]
              transition-transform group-hover:-translate-y-px
            "
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 40 40"
              className="h-7 w-7"
              fill="none"
            >
              <path
                d="M27 11H17.5C12.8056 11 9 14.8056 9 19.5V20.5C9 25.1944 12.8056 29 17.5 29H27"
                stroke="#F6F3EA"
                strokeWidth="4.5"
                strokeLinecap="square"
                strokeLinejoin="round"
              />
              <path
                d="M29.5 15H19.5C16.7386 15 14.5 17.2386 14.5 20C14.5 22.7614 16.7386 25 19.5 25H29.5"
                stroke="#37D4C8"
                strokeWidth="3.5"
                strokeLinecap="square"
                strokeLinejoin="round"
              />
              <path
                d="M18.5 17.5L22.5 20L18.5 22.5"
                stroke="#6F6CFF"
                strokeWidth="2.5"
                strokeLinecap="square"
                strokeLinejoin="round"
              />
              <path
                d="M23.5 26.5H29.5"
                stroke="#F6F3EA"
                strokeWidth="2.5"
                strokeLinecap="square"
              />
            </svg>
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-[color:var(--ntn-ink-deep)]">
            {copy.brand}
          </span>
        </Link>

        <div className="flex items-center gap-1.5">
          <a
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="GitHub repository"
            className="
              hidden sm:inline-flex items-center justify-center w-9 h-9
              rounded-[var(--ntn-rounded-md)]
              text-[color:var(--ntn-slate)]
              hover:text-[color:var(--ntn-ink-deep)] hover:bg-[color:var(--ntn-surface)]
              transition-colors
            "
          >
            <Github size={16} strokeWidth={1.8} />
          </a>
          <LanguageSwitcher
            locale={locale}
            pathnameWithoutLocale={pathnameWithoutLocale}
          />
          <ThemeToggle locale={locale} />
        </div>
      </div>
    </header>
  );
}
