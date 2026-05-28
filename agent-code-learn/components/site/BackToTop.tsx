'use client';

import { useEffect, useRef, useState } from 'react';
import { ArrowUp } from 'lucide-react';

interface BackToTopProps {
  label: string;
  /** 滚到这个像素值之后显示按钮。默认 600。 */
  threshold?: number;
}

/* 长文阅读到中段的常用 escape hatch：滚到底想回顶不用一直滑。
   设计权衡：
   - 只在 scrollY > threshold 出现，避免短文章首屏就显示一个漂浮按钮（视觉噪音）
   - rAF 节流，状态切换走 React state（每次只在「跨过阈值」一刻 setState 一次，不会每帧 render）
   - prefers-reduced-motion 时 scrollTo 用 'auto' 而不是 'smooth'，避免长滚动让人晕
   - 桌面右下；移动端右下偏上一点避开系统手势区 */
export default function BackToTop({ label, threshold = 600 }: BackToTopProps) {
  const [visible, setVisible] = useState(false);
  const rafRef = useRef<number | null>(null);
  const lastVisibleRef = useRef(false);

  useEffect(() => {
    function compute() {
      const v = window.scrollY > threshold;
      // 只在「跨越阈值」时触发 setState，避免每帧 render
      if (v !== lastVisibleRef.current) {
        lastVisibleRef.current = v;
        setVisible(v);
      }
    }
    function onScroll() {
      if (rafRef.current != null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        compute();
      });
    }
    compute();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [threshold]);

  function scrollTop() {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
  }

  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={scrollTop}
      className={[
        'fixed z-40 bottom-6 right-6 md:bottom-8 md:right-8',
        'w-10 h-10 rounded-full',
        'bg-[color:var(--ntn-canvas)] border border-[color:var(--ntn-hairline)]',
        'text-[color:var(--ntn-charcoal)]',
        'shadow-[0_4px_12px_0_rgba(15,15,15,0.10)]',
        'flex items-center justify-center',
        'transition-all duration-200',
        'hover:bg-[color:var(--ntn-primary)] hover:text-[color:var(--ntn-on-primary)] hover:border-transparent hover:-translate-y-0.5',
        visible
          ? 'opacity-100 translate-y-0 pointer-events-auto'
          : 'opacity-0 translate-y-2 pointer-events-none',
      ].join(' ')}
    >
      <ArrowUp size={16} strokeWidth={2} />
    </button>
  );
}
