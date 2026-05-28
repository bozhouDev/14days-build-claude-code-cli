'use client';

import { useEffect, useRef } from 'react';

/* 文章阅读进度条：贴在 TopNav 下面，2px 紫色填充。
   设计权衡：
   - 不用 useState 跟踪 scrollY —— 每滚动一帧都 setState 会让整个 React 树重 render，且帧率掉得很厉害。
     直接在 rAF 里改 DOM transform，把 React 完全旁路。
   - 用 transform: scaleX 而不是 width %，命中 GPU 合成层，永远 60fps。
   - 进度区间只算 <article> 的可视范围：从 article.top 进入视口算 0%，
     article.bottom 出视口算 100%。比 documentElement.scrollHeight 准得多
     —— 否则下面的 footer 会被算进 100% 里，读者读完正文进度条还在 80%。
   - prefers-reduced-motion 时仍然显示，因为它是「位置指示器」不是「装饰动画」，
     所有 transition 已经被 global.css 的兜底压成 0.001ms，不会闪。 */
export default function ReadingProgress() {
  const barRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const article = document.querySelector('article');
    const bar = barRef.current;
    if (!article || !bar) return;

    function update() {
      const rect = article!.getBoundingClientRect();
      const viewportH = window.innerHeight;
      const total = rect.height - viewportH;
      if (total <= 0) {
        bar!.style.transform = 'scaleX(1)';
        return;
      }
      // 已经滚出 article 顶部的距离 / 可滚动距离
      const scrolled = Math.min(Math.max(-rect.top, 0), total);
      const progress = scrolled / total;
      bar!.style.transform = `scaleX(${progress})`;
    }

    function onScroll() {
      if (rafRef.current != null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        update();
      });
    }

    update();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  return (
    /* 容器贴在 sticky TopNav 的下沿（top: 64px = nav 高度）。
       z-index 比 nav 低 1 档（z-30 vs nav 的 z-40），让 nav 始终盖在它上面。 */
    <div
      aria-hidden
      className="
        sticky top-16 z-30 h-[2px] w-full
        bg-[color:var(--ntn-hairline-soft)]
        pointer-events-none
      "
    >
      <div
        ref={barRef}
        className="
          h-full origin-left
          bg-gradient-to-r from-[color:var(--ntn-primary)] to-[color:var(--ntn-brand-pink)]
        "
        style={{ transform: 'scaleX(0)' }}
      />
    </div>
  );
}
