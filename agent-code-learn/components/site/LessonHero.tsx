import type { ReactNode } from 'react';
import { COPY } from '@/lib/copy';
import type { Locale } from '@/lib/i18n';
import type { LessonMeta } from '@/lib/lessons';

interface LessonHeroProps {
  locale: Locale;
  current?: LessonMeta;
  index: number; // 1-based
  total: number;
  title: string;
  description?: ReactNode;
}

/* 14 天进度点：每一天一个圆点，已学完=实心紫，当前=描边紫+光晕，未来=hairline 空圈。
   纯 SVG 实现避免 14 个 span 撑高度抖动；mobile 下点也不缩，能横排就够窄。 */
function ProgressDots({
  index,
  total,
}: {
  index: number;
  total: number;
}) {
  const W = 8;          // 单点直径
  const GAP = 6;        // 点间距
  const STRIDE = W + GAP;
  const width = total * STRIDE - GAP;
  const cy = W;         // 留 padding 给当前点的光晕

  return (
    <svg
      width={width}
      height={W * 2}
      viewBox={`0 0 ${width} ${W * 2}`}
      role="img"
      aria-label={`${index} / ${total}`}
      className="shrink-0"
    >
      {Array.from({ length: total }, (_, i) => {
        const cx = i * STRIDE + W / 2;
        const dayNum = i + 1;
        if (dayNum === index) {
          return (
            <g key={i}>
              <circle
                cx={cx}
                cy={cy}
                r={W / 2 + 2.5}
                fill="rgb(111 108 255 / 0.18)"
              />
              <circle
                cx={cx}
                cy={cy}
                r={W / 2}
                fill="var(--ntn-primary)"
              />
            </g>
          );
        }
        if (dayNum < index) {
          return (
            <circle
              key={i}
              cx={cx}
              cy={cy}
              r={W / 2}
              fill="var(--ntn-primary)"
              opacity={0.55}
            />
          );
        }
        return (
          <circle
            key={i}
            cx={cx}
            cy={cy}
            r={W / 2 - 1}
            fill="none"
            stroke="var(--ntn-hairline-strong)"
            strokeWidth={1}
          />
        );
      })}
    </svg>
  );
}

/* Stage 徽章：用 stage 字段（"Stage 01" / "Stage 02"）切换颜色身份。
   前 7 天 = lavender + 紫文字（呼应主品牌色）
   后 7 天 = peach + orange-deep 文字（暗示「升级 / 进阶」语义）
   读者扫过 14 天列表时，颜色切换就是课程节奏切换。 */
const STAGE_BADGE: Record<string, string> = {
  'Stage 01': 'bg-[color:var(--ntn-tint-lavender)] text-[color:var(--ntn-brand-purple-800)]',
  'Stage 02': 'bg-[color:var(--ntn-tint-peach)] text-[color:var(--ntn-brand-orange-deep)]',
};

/* 文章页的 hero 块：替换 page.tsx 里原来的 inline header。
   保持 server component，不引入任何 client 边界；description 走 ReactNode
   是因为 page.tsx 里把 inline `code` 反引号渲成 JSX 后传进来。 */
export default function LessonHero({
  locale,
  current,
  index,
  total,
  title,
  description,
}: LessonHeroProps) {
  const docsCopy = COPY[locale].docs;
  const stageClass = current ? STAGE_BADGE[current.stage] ?? STAGE_BADGE['Stage 01'] : '';
  // dayLabel 形如「Day 4 · 安全文件编辑」；左半边是「Day N」，右半边是当天主题。
  // 分裂出 dayPrefix 用在 hero 顶栏，让 h1 自己专心当大标题。
  const dayPrefix = current?.dayLabel.split('·')[0].trim();

  return (
    <header className="mb-10 pb-7 border-b border-[color:var(--ntn-hairline)]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 mb-5">
        {current && (
          <span
            className={[
              'inline-flex items-center px-2 py-0.5 rounded-[var(--ntn-rounded-sm)]',
              'text-[11px] font-semibold uppercase tracking-[0.12em]',
              stageClass,
            ].join(' ')}
          >
            {current.stage}
          </span>
        )}
        {dayPrefix && (
          <span className="text-[12.5px] font-semibold text-[color:var(--ntn-charcoal)]">
            {dayPrefix}
          </span>
        )}
        <span
          className="
            ml-auto inline-flex items-center gap-2.5
            text-[11px] font-[var(--ntn-font-mono)] tabular-nums
            text-[color:var(--ntn-stone)]
          "
        >
          <ProgressDots index={index} total={total} />
          <span>{docsCopy.dayOfTotal(index, total)}</span>
        </span>
      </div>

      <h1
        className="
          text-[36px] sm:text-[44px] md:text-[52px]
          leading-[1.08] font-semibold tracking-tight
          text-[color:var(--ntn-ink-deep)]
        "
      >
        {title}
      </h1>

      {description && (
        <p className="mt-5 text-[17px] leading-relaxed text-[color:var(--ntn-slate)] max-w-[720px]">
          {description}
        </p>
      )}
    </header>
  );
}
