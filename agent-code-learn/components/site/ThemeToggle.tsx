'use client';

import { useEffect, useState } from 'react';
import { Sun, Moon, Monitor } from 'lucide-react';

type Theme = 'system' | 'light' | 'dark';
const STORAGE_KEY = 'ntn-theme';

function readStored(): Theme {
  if (typeof window === 'undefined') return 'system';
  const s = window.localStorage.getItem(STORAGE_KEY);
  return s === 'light' || s === 'dark' ? s : 'system';
}

function systemPrefersDark(): boolean {
  if (typeof window === 'undefined') return false;
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function applyTheme(theme: Theme) {
  const isDark = theme === 'dark' || (theme === 'system' && systemPrefersDark());
  document.documentElement.classList.toggle('dark', isDark);
}

const CYCLE: Record<Theme, Theme> = {
  system: 'light',
  light: 'dark',
  dark: 'system',
};

const LABELS: Record<Theme, { zh: string; en: string }> = {
  system: { zh: '跟随系统', en: 'System' },
  light: { zh: '亮色', en: 'Light' },
  dark: { zh: '暗色', en: 'Dark' },
};

interface ThemeToggleProps {
  locale: 'zh' | 'en';
}

/* 三态循环：system → light → dark → system。
   - 初始 theme 来自 localStorage；layout.tsx 里的 inline script 已经在首屏前同步设置好 .dark class
   - mounted 之后才渲染 icon，避免 SSR/CSR 不一致警告
   - 监听 prefers-color-scheme 变化：仅当当前是 system 模式时跟随翻转 */
export default function ThemeToggle({ locale }: ThemeToggleProps) {
  const [theme, setTheme] = useState<Theme>('system');
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setTheme(readStored());
    setMounted(true);
  }, []);

  useEffect(() => {
    if (theme !== 'system') return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => applyTheme('system');
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [theme]);

  function cycle() {
    const next = CYCLE[theme];
    setTheme(next);
    try {
      if (next === 'system') window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* localStorage disabled — silently ignore，主题仅当前会话生效 */
    }
    applyTheme(next);
  }

  /* 未 mount 前给个占位，保证按钮位置/尺寸恒定，不抖。 */
  if (!mounted) {
    return (
      <span className="hidden sm:inline-flex w-9 h-9 rounded-[var(--ntn-rounded-md)]" aria-hidden />
    );
  }

  const Icon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor;
  const label = LABELS[theme][locale];

  return (
    <button
      type="button"
      onClick={cycle}
      title={label}
      aria-label={label}
      className="hidden sm:inline-flex items-center justify-center w-9 h-9 rounded-[var(--ntn-rounded-md)] text-[color:var(--ntn-slate)] hover:text-[color:var(--ntn-ink-deep)] hover:bg-[color:var(--ntn-surface)] transition-colors"
    >
      <Icon size={16} strokeWidth={1.8} />
    </button>
  );
}
