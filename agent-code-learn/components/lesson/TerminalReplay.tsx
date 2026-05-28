'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { usePathname } from 'next/navigation';
import { Play, RotateCcw } from 'lucide-react';
import { COPY } from '@/lib/copy';
import { localeFromPathname } from '@/lib/i18n';

interface OutputFrame {
  time: number;
  text: string;
}

interface TraceData {
  command: string;
  outputFrames: OutputFrame[];
}

interface TerminalReplayProps {
  tracePath: string;
}

function ansiToReact(text: string): React.ReactNode {
  const parts = text.split(/(\u001b\[\d+m)/);
  let cls = '';
  return parts.map((part, idx) => {
    if (part.startsWith('\u001b[')) {
      if (part === '\u001b[0m') cls = '';
      else if (part === '\u001b[33m')
        cls = 'text-[color:var(--ntn-brand-yellow)] font-semibold';
      else if (part === '\u001b[36m')
        cls = 'text-[color:var(--ntn-brand-purple-300)] font-semibold';
      else if (part === '\u001b[32m')
        cls = 'text-[color:var(--ntn-tint-mint)] font-semibold';
      return null;
    }
    return (
      <span key={idx} className={cls}>
        {part}
      </span>
    );
  });
}

function resolveTracePath(path: string, lang: 'zh' | 'en') {
  if (path.endsWith('.json')) {
    return path.replace(/\.json$/, `.${lang}.json`);
  }
  return path;
}

/* 亮色外壳（card）+ 深色屏（terminal screen）的组合。
   外层是 Notion 风浅阴影卡，内层 terminal 维持深 navy 底符合真实终端预期。 */
export default function TerminalReplay({ tracePath }: TerminalReplayProps) {
  const pathname = usePathname();
  const lang = useMemo(() => localeFromPathname(pathname), [pathname]);
  const copy = COPY[lang].terminal;

  const [trace, setTrace] = useState<TraceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [isPlaying, setIsPlaying] = useState(false);
  const [outputLines, setOutputLines] = useState<string[]>([]);
  const [frameIdx, setFrameIdx] = useState(-1);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const localized = resolveTracePath(tracePath, lang);
    fetch(localized)
      .then((r) => (r.ok ? r.json() : fetch(tracePath).then((rr) => rr.json())))
      .then((data: TraceData) => {
        setTrace(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [tracePath, lang]);

  useEffect(() => {
    if (!isPlaying || !trace || frameIdx < 0) return;
    if (frameIdx >= trace.outputFrames.length) {
      setIsPlaying(false);
      return;
    }
    const cur = trace.outputFrames[frameIdx];
    const prevTime = frameIdx === 0 ? 0 : trace.outputFrames[frameIdx - 1].time;
    const delay = Math.max(80, cur.time - prevTime);
    timerRef.current = setTimeout(() => {
      setOutputLines((p) => [...p, cur.text]);
      setFrameIdx((p) => p + 1);
    }, delay);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [isPlaying, frameIdx, trace]);

  const startReplay = () => {
    if (!trace) return;
    setOutputLines([]);
    setFrameIdx(0);
    setIsPlaying(true);
  };

  const reset = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setIsPlaying(false);
    setOutputLines([]);
    setFrameIdx(-1);
  };

  if (loading) {
    return (
      <div className="my-6 ntn-card-soft h-44 flex items-center justify-center text-[13px] text-[color:var(--ntn-slate)]">
        {copy.loading}
      </div>
    );
  }

  return (
    <div className="my-8 rounded-[var(--ntn-rounded-lg)] border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)] ntn-shadow-2 overflow-hidden">
      {/* 亮色顶栏：traffic dots + 文件名 + 紫色 Run 按钮 */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-[color:var(--ntn-surface-soft)] border-b border-[color:var(--ntn-hairline-soft)]">
        <div className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-[color:var(--ntn-tint-rose)]" />
          <span className="w-2.5 h-2.5 rounded-full bg-[color:var(--ntn-tint-yellow-bold)]" />
          <span className="w-2.5 h-2.5 rounded-full bg-[color:var(--ntn-tint-mint)]" />
          <span className="ml-3 text-[11.5px] text-[color:var(--ntn-slate)] font-medium">
            {copy.title}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {frameIdx === -1 ? (
            <button
              onClick={startReplay}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-[var(--ntn-rounded-md)] bg-[color:var(--ntn-primary)] text-[color:var(--ntn-on-primary)] text-[11.5px] font-semibold hover:bg-[color:var(--ntn-primary-pressed)] transition-colors"
            >
              <Play size={10} fill="currentColor" />
              {copy.run}
            </button>
          ) : (
            <button
              onClick={reset}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-[var(--ntn-rounded-md)] border border-[color:var(--ntn-hairline-strong)] text-[color:var(--ntn-ink-deep)] text-[11.5px] font-semibold hover:bg-[color:var(--ntn-surface)] transition-colors"
            >
              <RotateCcw size={10} />
              {copy.reset}
            </button>
          )}
        </div>
      </div>

      {/* 深色 terminal screen */}
      <div className="p-4 font-[var(--ntn-font-mono)] text-[13px] leading-[1.75] min-h-[170px] bg-[color:var(--ntn-brand-navy-deep)] text-[color:var(--ntn-on-dark-muted)]">
        <div className="mb-2">
          <span className="text-[color:var(--ntn-brand-purple-300)] mr-2">$</span>
          <span className="text-[color:var(--ntn-on-dark)]">{trace?.command}</span>
        </div>
        {outputLines.map((line, idx) => (
          <div key={idx} className="ntn-anim-in whitespace-pre-wrap">
            {ansiToReact(line)}
          </div>
        ))}
        {isPlaying && (
          <span className="inline-block w-1.5 h-3.5 bg-[color:var(--ntn-brand-purple-300)] align-middle ml-0.5 animate-pulse" />
        )}
      </div>
    </div>
  );
}
