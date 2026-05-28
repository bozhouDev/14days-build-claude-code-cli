import Link from 'next/link';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { COPY } from '@/lib/copy';
import { localizedHref } from '@/lib/href';
import type { Locale } from '@/lib/i18n';
import type { LessonMeta } from '@/lib/lessons';

interface LessonPagerProps {
  locale: Locale;
  prev?: LessonMeta;
  next?: LessonMeta;
}

interface PagerCardProps {
  locale: Locale;
  direction: 'prev' | 'next';
  lesson?: LessonMeta;
  label: string;
  comingSoonLabel: string;
}

/* 单边卡片：
   - 有 lesson 且 ready：渲染 Link，hover 时整张卡浮起一点 + 边框换成 primary
   - 有 lesson 但 locked：用 div 占位，提示「即将上线」，不可点
   - 没 lesson（首篇/末篇）：渲染空 div 撑住 grid 占位，避免布局塌陷 */
function PagerCard({
  locale,
  direction,
  lesson,
  label,
  comingSoonLabel,
}: PagerCardProps) {
  if (!lesson) {
    return <div className="hidden md:block" aria-hidden />;
  }

  const isReady = lesson.status === 'ready';
  const Icon = direction === 'prev' ? ArrowLeft : ArrowRight;
  const align = direction === 'prev' ? 'text-left items-start' : 'text-right items-end';
  // dayLabel 形如「Day 4 · 安全文件编辑」；左边是 Day N，右边是主题
  const [dayPart, topicPart] = lesson.dayLabel.split('·').map((s) => s.trim());

  const body = (
    <div className={`flex flex-col gap-1 ${align}`}>
      <span
        className={`inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] ${
          isReady
            ? 'text-[color:var(--ntn-stone)] group-hover:text-[color:var(--ntn-primary-deep)]'
            : 'text-[color:var(--ntn-stone)]'
        } transition-colors`}
      >
        {direction === 'prev' && <Icon size={12} strokeWidth={2} />}
        {isReady ? label : comingSoonLabel}
        {direction === 'next' && <Icon size={12} strokeWidth={2} />}
      </span>
      <span
        className={`text-[11px] font-[var(--ntn-font-mono)] ${
          isReady ? 'text-[color:var(--ntn-slate)]' : 'text-[color:var(--ntn-muted)]'
        }`}
      >
        {dayPart}
      </span>
      <span
        className={`text-[14.5px] font-semibold leading-snug ${
          isReady
            ? 'text-[color:var(--ntn-ink-deep)] group-hover:text-[color:var(--ntn-primary-deep)]'
            : 'text-[color:var(--ntn-muted)]'
        } transition-colors`}
      >
        {topicPart}
      </span>
    </div>
  );

  const sharedClassName = [
    'group block rounded-[var(--ntn-rounded-lg)]',
    'border border-[color:var(--ntn-hairline)]',
    'bg-[color:var(--ntn-canvas)] px-5 py-4',
    'transition-all',
  ].join(' ');

  if (isReady) {
    return (
      <Link
        href={localizedHref(`/docs/${lesson.slug}`, locale)}
        className={`${sharedClassName} hover:-translate-y-0.5 hover:border-[color:var(--ntn-primary)] hover:bg-[color:var(--ntn-tint-lavender)] hover:shadow-[0_4px_12px_0_rgba(111,108,255,0.12)]`}
      >
        {body}
      </Link>
    );
  }
  return (
    <div
      className={`${sharedClassName} bg-[color:var(--ntn-surface-soft)] cursor-not-allowed`}
      aria-disabled
    >
      {body}
    </div>
  );
}

/* 文章末尾 prev/next pager：两列卡片，长篇读完天然过渡到下一章。
   首篇时 prev 卡变隐形占位（mobile 上索性不渲染、桌面上用 md:block aria-hidden 撑布局）。 */
export default function LessonPager({ locale, prev, next }: LessonPagerProps) {
  const copy = COPY[locale].docs;
  if (!prev && !next) return null;

  return (
    <nav
      aria-label={locale === 'zh' ? '章节翻页' : 'Lesson navigation'}
      className="mt-16 pt-8 border-t border-[color:var(--ntn-hairline)] grid grid-cols-1 md:grid-cols-2 gap-3"
    >
      <PagerCard
        locale={locale}
        direction="prev"
        lesson={prev}
        label={copy.prevLessonLabel}
        comingSoonLabel={copy.comingSoonLabel}
      />
      <PagerCard
        locale={locale}
        direction="next"
        lesson={next}
        label={copy.nextLessonLabel}
        comingSoonLabel={copy.comingSoonLabel}
      />
    </nav>
  );
}
