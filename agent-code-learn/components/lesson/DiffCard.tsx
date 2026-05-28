'use client';

import { useEffect, useState } from 'react';
import { FileDiff } from 'lucide-react';

interface DiffCardProps {
  diffPath: string;
  title: string;
  description: string;
}

/* Notion 风：亮色头 + 浅 surface diff 区。
   +/- 行用 mint / rose 浅 tint；@@ hunk 行用 lavender + 紫色文字。 */
export default function DiffCard({ diffPath, title, description }: DiffCardProps) {
  const [diffText, setDiffText] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(diffPath)
      .then((res) => res.text())
      .then((text) => {
        setDiffText(text);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [diffPath]);

  if (loading) {
    return (
      <div className="my-6 ntn-card-soft h-40 flex items-center justify-center text-[13px] text-[color:var(--ntn-slate)]">
        loading…
      </div>
    );
  }

  const lines = diffText.split('\n');
  const fileLine = lines.find((l) => l.startsWith('+++'))?.replace(/^\+\+\+\s*/, '') || '';

  return (
    <div className="my-8 rounded-[var(--ntn-rounded-lg)] border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)] overflow-hidden">
      <div className="px-5 py-4 border-b border-[color:var(--ntn-hairline)] flex flex-col md:flex-row md:items-start md:justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5 flex-wrap">
            <FileDiff size={13} className="text-[color:var(--ntn-primary)]" />
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-[var(--ntn-rounded-xs)] bg-[color:var(--ntn-tint-lavender)] text-[color:var(--ntn-brand-purple-800)] text-[10.5px] font-semibold uppercase tracking-wider">
              diff
            </span>
            {fileLine && (
              <span className="text-[11.5px] font-[var(--ntn-font-mono)] text-[color:var(--ntn-charcoal)] truncate">
                {fileLine}
              </span>
            )}
          </div>
          <h4 className="text-[15px] font-semibold text-[color:var(--ntn-ink-deep)] leading-tight">
            {title}
          </h4>
          <p className="mt-1 text-[13px] leading-relaxed text-[color:var(--ntn-slate)] md:max-w-[80%]">
            {description}
          </p>
        </div>
      </div>

      {/* 注意：这里不用 <pre>。一旦 DiffCard 出现在 .ntn-prose 里，
          .ntn-prose pre { background: var(--ntn-ink-deep) } 会把整个 diff 染黑，
          把内行的 mint / rose tint 全部吃掉。
          内层每行的 whitespace-pre 已经在最右侧 span 上保留了原始空白。 */}
      <div className="font-[var(--ntn-font-mono)] text-[12.5px] leading-[1.7] max-h-[480px] overflow-auto bg-[color:var(--ntn-surface-soft)]">
        {lines.map((line, idx) => {
          if (idx === lines.length - 1 && line === '') return null;

          let rowClass = 'text-[color:var(--ntn-charcoal)]';
          let leftBar = 'border-l-transparent';
          let sign = ' ';
          let display = line;

          if (line.startsWith('+++') || line.startsWith('---')) {
            rowClass = 'text-[color:var(--ntn-stone)]';
          } else if (line.startsWith('@@')) {
            rowClass = 'text-[color:var(--ntn-primary-deep)] bg-[color:var(--ntn-tint-lavender)] font-semibold';
          } else if (line.startsWith('+')) {
            rowClass = 'text-[color:var(--ntn-success)] bg-[color:var(--ntn-tint-mint)]';
            leftBar = 'border-l-[color:var(--ntn-success)]';
            sign = '+';
            display = line.slice(1);
          } else if (line.startsWith('-')) {
            rowClass = 'text-[color:var(--ntn-error)] bg-[color:var(--ntn-tint-rose)]';
            leftBar = 'border-l-[color:var(--ntn-error)]';
            sign = '-';
            display = line.slice(1);
          }

          return (
            <div
              key={idx}
              className={`flex border-l-2 ${leftBar} ${rowClass}`}
            >
              <span className="select-none w-9 text-right pr-3 pl-1 text-[color:var(--ntn-stone)] shrink-0">
                {idx + 1}
              </span>
              <span className="select-none w-4 text-center text-[color:var(--ntn-stone)] shrink-0">
                {sign === ' ' ? '' : sign}
              </span>
              <span className="whitespace-pre flex-1 pr-3">{display}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
