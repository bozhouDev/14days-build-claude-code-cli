import Link from 'next/link';
import { Github, ArrowRight } from 'lucide-react';
import { COPY } from '@/lib/copy';
import { localizedHref } from '@/lib/href';
import type { Locale } from '@/lib/i18n';

interface FooterProps {
  locale: Locale;
}

/* TopNav 已经写死了同一个 repo url，footer 这里复用同一个常量来源。
   仓库地址只有一份事实，重复硬编码两处迟早不一致 —— 但目前两个文件互不依赖，
   引入一个单独的常量模块成本过高，先就近写明它对齐 TopNav.tsx 顶部的 REPO_URL。 */
const REPO_URL = 'https://github.com/anthropics/claude-code';

/* 文档站底部：左侧 logo + 一行 smallprint，右侧两条最小出口（源码 + 回 Day 1）。
   不做多列 link grid（landing / compare / glossary 都已下线），把出口收敛到一行内。 */
export default function Footer({ locale }: FooterProps) {
  const copy = COPY[locale].chrome;

  return (
    <footer className="mt-20 border-t border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)]">
      <div
        className="
          max-w-[1400px] mx-auto px-6 py-6
          flex flex-col sm:flex-row sm:items-center sm:justify-between
          gap-3 text-[12px]
        "
      >
        <div className="flex items-center gap-2.5 text-[color:var(--ntn-stone)]">
          {/* 小一号的 N logo，和 TopNav 视觉同源但不抢戏 */}
          <span
            className="
              w-5 h-5 rounded-[var(--ntn-rounded-sm)]
              bg-[color:var(--ntn-logo-bg)] text-[color:var(--ntn-logo-fg)]
              flex items-center justify-center
              font-bold text-[10.5px] leading-none
              shadow-[inset_0_1px_0_rgba(255,255,255,0.10)]
            "
            aria-hidden
          >
            N
          </span>
          <span>{copy.footerSmallprint}</span>
        </div>

        <div className="flex items-center gap-1">
          <a
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="
              inline-flex items-center gap-1.5 px-2 py-1
              rounded-[var(--ntn-rounded-sm)]
              text-[color:var(--ntn-slate)]
              hover:text-[color:var(--ntn-ink-deep)] hover:bg-[color:var(--ntn-surface)]
              transition-colors
            "
          >
            <Github size={13} strokeWidth={1.8} />
            <span>{copy.footerLinks.repo}</span>
          </a>
          <Link
            href={localizedHref('/docs/day-01-hello-agent', locale)}
            className="
              inline-flex items-center gap-1.5 px-2 py-1
              rounded-[var(--ntn-rounded-sm)]
              text-[color:var(--ntn-slate)]
              hover:text-[color:var(--ntn-primary-deep)] hover:bg-[color:var(--ntn-tint-lavender)]
              transition-colors
            "
          >
            <span>{copy.footerLinks.start}</span>
            <ArrowRight size={13} strokeWidth={1.8} />
          </Link>
        </div>
      </div>
    </footer>
  );
}
