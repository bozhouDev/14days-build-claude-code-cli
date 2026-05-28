import './global.css';
import type { ReactNode } from 'react';
import { Geist } from 'next/font/google';
import Script from 'next/script';

/* Notion-Sans 用 Geist Variable 顶上；通过 CSS 变量 --font-geist 暴露给 global.css 的
   --ntn-font-sans fallback 链。中文 fallback 走 PingFang SC / Hiragino Sans GB。 */
const geist = Geist({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-geist',
});

export const metadata = {
  title: 'BuildCC',
  description:
    'Build a Claude Code style Agent from scratch — Python, 14 days, interactive lessons.',
  icons: {
    icon: '/brand/buildcc-icon.svg',
  },
};

/* 在 React hydrate 之前同步设置 .dark class，避免暗色用户首屏白闪（FOUC）。
   读 localStorage['ntn-theme']：'light' / 'dark' / 'system' / null（=system）。 */
const themeInitScript = `(function(){try{
  var s = localStorage.getItem('ntn-theme');
  var d = s === 'dark' || ((!s || s === 'system') && window.matchMedia('(prefers-color-scheme: dark)').matches);
  if (d) document.documentElement.classList.add('dark');
}catch(e){}})();`;

/* Root layout 只挂 <html>/<body>；i18n 上下文与 Fumadocs Provider 在 [lang]/layout.tsx 接管。 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh" className={geist.variable} suppressHydrationWarning>
      <body className="min-h-screen antialiased bg-[color:var(--ntn-canvas)] text-[color:var(--ntn-ink)]">
        <Script
          id="ntn-theme-init"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: themeInitScript }}
        />
        {children}
      </body>
    </html>
  );
}
