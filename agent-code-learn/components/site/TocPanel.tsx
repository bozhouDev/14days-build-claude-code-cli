'use client';

import { useEffect, useState } from 'react';

interface TocItem {
  title: React.ReactNode;
  url: string;
  depth: number;
}

interface TocPanelProps {
  toc: TocItem[];
  label: string;
}

/* Notion 风的「本章目录」右栏：
   - 锚点链接 + 按 depth 缩进（h2 不缩、h3 缩 12px、h4 缩 24px）
   - IntersectionObserver 做 scroll-spy：当前进入视口最靠上的小节高亮 + 左侧 2px 紫色 indicator
   - 左侧整列 hairline 竖线，让目录像一根「时间轴」
   - 空 toc 直接不渲染，让正文撑满 */
export default function TocPanel({ toc, label }: TocPanelProps) {
  const [activeId, setActiveId] = useState<string>('');

  useEffect(() => {
    if (toc.length === 0) return;
    const ids = toc.map((item) => item.url.replace(/^#/, ''));

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort(
            (a, b) =>
              a.target.getBoundingClientRect().top -
              b.target.getBoundingClientRect().top,
          );
        if (visible.length > 0) {
          setActiveId(visible[0].target.id);
        }
      },
      {
        /* 顶部 88px 让 sticky nav 的 64px + 一点呼吸；
           底部 -65% 让 active 在标题进入上 1/3 屏时就触发，符合阅读视线 */
        rootMargin: '-88px 0px -65% 0px',
        threshold: 0,
      },
    );

    const els: HTMLElement[] = [];
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) {
        observer.observe(el);
        els.push(el);
      }
    });

    /* 首屏初始化：如果还没有 active 且页面已经滚动过，挑离视口顶最近的那条作为 active */
    if (!activeId && els.length > 0) {
      const initial = els.find((el) => el.getBoundingClientRect().top > 100) ?? els[0];
      setActiveId(initial.id);
    }

    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toc]);

  if (toc.length === 0) return null;

  return (
    <aside className="w-[240px] shrink-0 hidden xl:block">
      <div className="sticky top-[72px] py-8 pl-2 max-h-[calc(100vh-72px)] overflow-y-auto">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[color:var(--ntn-primary-deep)] mb-3">
          {label}
        </div>
        <ul className="border-l border-[color:var(--ntn-hairline)]">
          {toc.map((item) => {
            const id = item.url.replace(/^#/, '');
            const active = id === activeId;
            const indent = Math.max(0, item.depth - 2) * 12;
            return (
              <li key={item.url} className="relative">
                {active && (
                  <span className="absolute -left-[1px] top-1.5 bottom-1.5 w-[2px] rounded-full bg-[color:var(--ntn-primary)]" />
                )}
                <a
                  href={item.url}
                  className={[
                    'block py-1 pr-2 text-[12.5px] leading-snug transition-colors',
                    active
                      ? 'text-[color:var(--ntn-primary-deep)] font-medium'
                      : 'text-[color:var(--ntn-steel)] hover:text-[color:var(--ntn-ink-deep)]',
                  ].join(' ')}
                  style={{ paddingLeft: `${12 + indent}px` }}
                >
                  {item.title}
                </a>
              </li>
            );
          })}
        </ul>
      </div>
    </aside>
  );
}
