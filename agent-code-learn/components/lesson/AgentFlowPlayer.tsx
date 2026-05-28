'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePathname } from 'next/navigation';
import { Play, Pause, ChevronLeft, ChevronRight, RotateCcw, Cpu, Wrench, Database, Terminal } from 'lucide-react';
import { COPY } from '@/lib/copy';
import { localeFromPathname } from '@/lib/i18n';
import MessageInspector from './MessageInspector';

interface FlowNode {
  id: string;
  label: string;
  type: 'process' | 'data' | 'model' | 'tool';
}

interface FlowFrame {
  step: number;
  focus: string;
  caption: string;
  messages: any[];
  modelResponse?: any;
}

interface FlowData {
  nodes: FlowNode[];
  frames: FlowFrame[];
}

interface AgentFlowPlayerProps {
  /* 外部传 i18n 形态的路径模板，组件按当前语言取 */
  flowPath: string;
}

function resolveFlowPath(path: string, lang: 'zh' | 'en'): string {
  if (path.endsWith('.json')) {
    return path.replace(/\.json$/, `.${lang}.json`);
  }
  return path;
}

const ICONS: Record<FlowNode['type'], React.ComponentType<{ size?: number }>> = {
  process: Terminal,
  data: Database,
  model: Cpu,
  tool: Wrench,
};

export default function AgentFlowPlayer({ flowPath }: AgentFlowPlayerProps) {
  const pathname = usePathname();
  const lang = useMemo(() => localeFromPathname(pathname), [pathname]);
  const copy = COPY[lang].flow;

  const [flow, setFlow] = useState<FlowData | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentStep, setCurrentStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  useEffect(() => {
    const path = resolveFlowPath(flowPath, lang);
    fetch(path)
      .then((r) => (r.ok ? r.json() : fetch(flowPath).then((rr) => rr.json())))
      .then((data: FlowData) => {
        setFlow(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [flowPath, lang]);

  useEffect(() => {
    if (!isPlaying || !flow) return;
    const timer = setInterval(() => {
      setCurrentStep((prev) => {
        if (prev >= flow.frames.length - 1) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, 2400);
    return () => clearInterval(timer);
  }, [isPlaying, flow]);

  if (loading || !flow) {
    return (
      <div className="my-6 ntn-card-soft h-72 flex items-center justify-center text-[13px] text-[color:var(--ntn-slate)]">
        loading flow…
      </div>
    );
  }

  const currentFrame = flow.frames[currentStep];
  const activeStepIdx = flow.nodes.findIndex((n) => n.id === currentFrame.focus);

  return (
    <div className="my-8 ntn-card overflow-hidden">
      <div className="px-5 py-4 border-b border-[color:var(--ntn-hairline)] flex items-start justify-between gap-4">
        <div>
          <div className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[color:var(--ntn-primary-deep)] mb-1">
            flow player
          </div>
          <h4 className="text-[15px] font-semibold text-[color:var(--ntn-ink-deep)]">
            {copy.title}
          </h4>
          <p className="text-[12.5px] text-[color:var(--ntn-slate)] mt-0.5">
            {copy.hint}
          </p>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={() => {
              setIsPlaying(false);
              setCurrentStep((p) => Math.max(0, p - 1));
            }}
            disabled={currentStep === 0}
            title={copy.prev}
            className="p-2 rounded-[var(--ntn-rounded-md)] text-[color:var(--ntn-slate)] hover:bg-[color:var(--ntn-surface)] hover:text-[color:var(--ntn-ink-deep)] disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
          >
            <ChevronLeft size={14} />
          </button>
          <button
            onClick={() => setIsPlaying(!isPlaying)}
            className={[
              'flex items-center gap-1.5 px-3 py-1.5 rounded-[var(--ntn-rounded-md)] text-[12.5px] font-semibold transition-colors',
              isPlaying
                ? 'border border-[color:var(--ntn-hairline-strong)] text-[color:var(--ntn-ink-deep)] bg-[color:var(--ntn-canvas)]'
                : 'bg-[color:var(--ntn-primary)] text-[color:var(--ntn-on-primary)] hover:bg-[color:var(--ntn-primary-pressed)]',
            ].join(' ')}
          >
            {isPlaying ? <Pause size={11} fill="currentColor" /> : <Play size={11} fill="currentColor" />}
            {isPlaying ? copy.pause : copy.play}
          </button>
          <button
            onClick={() => {
              setIsPlaying(false);
              setCurrentStep((p) => Math.min(flow.frames.length - 1, p + 1));
            }}
            disabled={currentStep === flow.frames.length - 1}
            title={copy.next}
            className="p-2 rounded-[var(--ntn-rounded-md)] text-[color:var(--ntn-slate)] hover:bg-[color:var(--ntn-surface)] hover:text-[color:var(--ntn-ink-deep)] disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
          >
            <ChevronRight size={14} />
          </button>
          <button
            onClick={() => {
              setIsPlaying(false);
              setCurrentStep(0);
            }}
            title={copy.reset}
            className="p-2 rounded-[var(--ntn-rounded-md)] text-[color:var(--ntn-slate)] hover:bg-[color:var(--ntn-surface)] hover:text-[color:var(--ntn-ink-deep)] transition-colors"
          >
            <RotateCcw size={12} />
          </button>
        </div>
      </div>

      {/* 节点网格 */}
      <div className="px-5 py-5 grid grid-cols-2 md:grid-cols-4 gap-3 bg-[color:var(--ntn-surface-soft)]">
        {flow.nodes.map((node, idx) => {
          const isActive = idx === activeStepIdx;
          const isDone = activeStepIdx > -1 && idx < activeStepIdx;
          const Icon = ICONS[node.type] ?? Terminal;
          const cls = isActive
            ? 'ntn-node-active'
            : isDone
              ? 'ntn-node-done'
              : 'ntn-node-idle';

          return (
            <button
              key={node.id}
              onClick={() => {
                setIsPlaying(false);
                const i = flow.frames.findIndex((f) => f.focus === node.id);
                if (i !== -1) setCurrentStep(i);
              }}
              className={`flex items-center gap-3 p-3 rounded-[var(--ntn-rounded-md)] text-left transition-all ${cls}`}
            >
              <div
                className={[
                  'w-8 h-8 rounded-[var(--ntn-rounded-md)] flex items-center justify-center shrink-0',
                  isActive
                    ? 'bg-[color:var(--ntn-primary)] text-[color:var(--ntn-on-primary)]'
                    : isDone
                      ? 'bg-[color:var(--ntn-canvas)] text-[color:var(--ntn-success)]'
                      : 'bg-[color:var(--ntn-surface)] text-[color:var(--ntn-stone)]',
                ].join(' ')}
              >
                <Icon size={14} />
              </div>
              <div className="min-w-0">
                <div className="text-[10.5px] font-semibold text-[color:var(--ntn-stone)] uppercase tracking-[0.1em]">
                  step {idx + 1}
                </div>
                <div
                  className={[
                    'text-[13px] font-semibold truncate',
                    isActive
                      ? 'text-[color:var(--ntn-primary-deep)]'
                      : 'text-[color:var(--ntn-ink-deep)]',
                  ].join(' ')}
                >
                  {node.label}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Caption + 帧计数 —— sky tint，左边 2px 紫色 indicator */}
      <div className="px-5 pt-5 pb-5">
        <div className="rounded-[var(--ntn-rounded-md)] ntn-tint-sky px-4 py-3 border-l-2 border-[color:var(--ntn-primary)]">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[color:var(--ntn-primary-deep)]">
              {copy.frameLabel(currentStep + 1, flow.frames.length)}
            </span>
          </div>
          <p className="text-[13.5px] leading-relaxed text-[color:var(--ntn-charcoal)]">
            {currentFrame.caption}
          </p>
        </div>
      </div>

      {/* Message Inspector */}
      <div className="px-5 pb-5">
        <MessageInspector
          messages={currentFrame.messages}
          modelResponse={currentFrame.modelResponse}
          locale={lang}
        />
      </div>
    </div>
  );
}
