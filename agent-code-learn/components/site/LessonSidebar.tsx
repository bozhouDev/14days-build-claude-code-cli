import Link from 'next/link';
import { Lock } from 'lucide-react';
import { COPY } from '@/lib/copy';
import { localizedHref } from '@/lib/href';
import { getLessons, type LessonMeta } from '@/lib/lessons';
import type { Locale } from '@/lib/i18n';

interface LessonSidebarProps {
  locale: Locale;
  currentSlug?: string;
}

/* 课程阶段对应的中英文小标题。和 lessons.ts 里 stage 字段对齐：
   Stage 01 = 前 7 天「能用的单 Agent CLI」
   Stage 02 = 后 7 天「升级成完整 Claude Code 风格 harness」 */
const STAGE_TITLES: Record<string, Record<Locale, string>> = {
  'Stage 01': { zh: '能用的 Agent CLI', en: 'A working Agent CLI' },
  'Stage 02': { zh: '完整 harness', en: 'Full harness upgrade' },
};

function statusDot(status: LessonMeta['status']) {
  switch (status) {
    case 'ready':
      return 'bg-[color:var(--ntn-primary)] shadow-[0_0_0_2px_rgba(111,108,255,0.18)]';
    case 'soon':
      return 'bg-[color:var(--ntn-warning)]';
    default:
      return 'bg-[color:var(--ntn-muted)]';
  }
}

/* lessons.ts 给的是扁平数组，这里按 stage 做一次原地分组而不动 lessons.ts 本身。
   保留原数组顺序（已经按 day 排好），group 出现的顺序就是用户阅读顺序。 */
function groupByStage(lessons: LessonMeta[]): { stage: string; items: LessonMeta[] }[] {
  const order: string[] = [];
  const map = new Map<string, LessonMeta[]>();
  for (const l of lessons) {
    if (!map.has(l.stage)) {
      order.push(l.stage);
      map.set(l.stage, []);
    }
    map.get(l.stage)!.push(l);
  }
  return order.map((stage) => ({ stage, items: map.get(stage)! }));
}

/* Notion 风左侧 sidebar：
   - 白底、hairline、active = lavender 高亮 + 左侧紫色 indicator
   - 按 stage 分组：每个 stage 一个小 label + 一个虚线段，让 Day 1-7 vs Day 8-14 的课程节奏可见
   - locked item 显式画一把锁图标，不只靠灰色文字
   - ready item 的圆点带一层 18% 的 primary 光晕，扫一眼就知道哪些章节已发布 */
export default function LessonSidebar({ locale, currentSlug }: LessonSidebarProps) {
  const copy = COPY[locale].docs;
  const groups = groupByStage(getLessons(locale));

  return (
    <aside className="w-[260px] shrink-0 hidden lg:block">
      <div className="sticky top-[64px] py-8 pr-4 max-h-[calc(100vh-64px)] overflow-y-auto ntn-scroll">
        <div className="mb-6">
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[color:var(--ntn-primary-deep)]">
            curriculum
          </div>
          <h2 className="text-[15px] font-semibold text-[color:var(--ntn-ink-deep)] mt-1">
            {copy.sidebarHeading}
          </h2>
          <p className="text-[12px] text-[color:var(--ntn-steel)] mt-1">
            {copy.sidebarSubheading}
          </p>
        </div>

        <nav className="space-y-5">
          {groups.map((group, gi) => {
            const stageTitle = STAGE_TITLES[group.stage]?.[locale];
            return (
              <div key={group.stage}>
                {/* stage header：一条 hairline + 阶段编号 + 阶段小标题。第一个 group 不画分隔线，省视觉重量。 */}
                <div className={gi > 0 ? 'pt-4 border-t border-[color:var(--ntn-hairline-soft)]' : ''}>
                  <div className="flex items-baseline justify-between px-2.5 mb-2">
                    <span className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-[color:var(--ntn-stone)]">
                      {group.stage}
                    </span>
                    {stageTitle && (
                      <span className="text-[10.5px] text-[color:var(--ntn-stone)]">
                        {stageTitle}
                      </span>
                    )}
                  </div>

                  <ul className="space-y-0.5">
                    {group.items.map((lesson) => {
                      const active = lesson.slug === currentSlug;
                      const ready = lesson.status === 'ready';
                      const className = [
                        'relative flex items-center gap-2 px-2.5 py-1.5 rounded-[var(--ntn-rounded-md)] text-[13px] transition-colors',
                        active
                          ? 'bg-[color:var(--ntn-tint-lavender)] text-[color:var(--ntn-primary-deep)] font-semibold'
                          : ready
                            ? 'text-[color:var(--ntn-charcoal)] hover:bg-[color:var(--ntn-surface)] hover:text-[color:var(--ntn-ink-deep)]'
                            : 'text-[color:var(--ntn-stone)] cursor-default',
                      ].join(' ');

                      const Inner = (
                        <>
                          {active && (
                            <span className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-full bg-[color:var(--ntn-primary)]" />
                          )}
                          <span
                            className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDot(lesson.status)}`}
                          />
                          <span className="truncate">{lesson.dayLabel}</span>
                          {lesson.status === 'locked' && (
                            <Lock
                              size={11}
                              strokeWidth={2}
                              className="ml-auto shrink-0 text-[color:var(--ntn-muted)]"
                              aria-label={locale === 'zh' ? '尚未发布' : 'not yet published'}
                            />
                          )}
                        </>
                      );

                      if (ready) {
                        return (
                          <li key={lesson.slug}>
                            <Link
                              href={localizedHref(`/docs/${lesson.slug}`, locale)}
                              className={className}
                            >
                              {Inner}
                            </Link>
                          </li>
                        );
                      }
                      return (
                        <li key={lesson.slug} className={className}>
                          {Inner}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              </div>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}
