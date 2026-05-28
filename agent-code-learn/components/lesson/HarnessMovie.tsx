'use client';

import { Fragment, useEffect, useMemo, useState } from 'react';
import { usePathname } from 'next/navigation';
import {
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Play,
  Pause,
  RotateCcw,
  Inbox,
  Cpu,
  ShieldCheck,
  Globe2,
  Database,
  ArrowRight,
  Check,
  X,
  HelpCircle,
} from 'lucide-react';
import { COPY } from '@/lib/copy';
import { localeFromPathname } from '@/lib/i18n';
import MessageInspector from './MessageInspector';

/* ---------------- Types ---------------- */

type LaneId = 'context' | 'model' | 'gate' | 'world';
type CardKind = 'user_input' | 'tool_use' | 'tool_result' | 'final';
type GateState = 'open' | 'blocked' | 'ask';

interface MovieMessage {
  role: string;
  content: string;
  tool_call_id?: string;
}

interface MovieModelResponse {
  text?: string;
  tool_calls?: Array<{ id: string; name: string; arguments: Record<string, unknown> }>;
  stop_reason?: string;
}

interface MovieFrame {
  step: number;
  focus: LaneId;
  card?: { kind: CardKind; label: string };
  gate?: { rule: string; state: GateState; target?: string };
  ledger?: { delta: string };
  caption: string;
  messages: MovieMessage[];
  modelResponse?: MovieModelResponse;
}

interface MovieData {
  title: string;
  task: string;
  lanes: Record<LaneId, string>;
  frames: MovieFrame[];
}

interface HarnessMovieProps {
  /* /lessons/day-01/movie.json 形态。组件按当前语言加 .zh / .en 后缀。 */
  moviePath: string;
}

/* ---------------- Tokens ---------------- */

/* Lane 颜色身份：和 MessageInspector 的 ROLE_STYLE 对齐，
   user=lavender / assistant=sky / tool=peach / tool_result=mint —— 这样
   读者在 movie 上看到的色相和下面 inspector 里的消息颜色是同一套语义。 */
const LANE_TINT: Record<LaneId, { active: string; idle: string; label: string }> = {
  context: {
    active: 'bg-[color:var(--ntn-tint-lavender)] border-[color:var(--ntn-primary)]',
    idle: 'bg-[color:var(--ntn-canvas)] border-[color:var(--ntn-hairline)]',
    label: 'text-[color:var(--ntn-brand-purple-800)]',
  },
  model: {
    active: 'bg-[color:var(--ntn-tint-sky)] border-[color:var(--ntn-link-blue-pressed)]',
    idle: 'bg-[color:var(--ntn-canvas)] border-[color:var(--ntn-hairline)]',
    label: 'text-[color:var(--ntn-link-blue-pressed)]',
  },
  gate: {
    /* gate active 用 cream/yellow 而不是 mint，强调它是“检查点 / 决断时刻”，
       具体放行/拒绝/问问，由内部 gate.state 再上色。 */
    active: 'bg-[color:var(--ntn-tint-cream)] border-[color:var(--ntn-stone)]',
    idle: 'bg-[color:var(--ntn-canvas)] border-[color:var(--ntn-hairline)]',
    label: 'text-[color:var(--ntn-charcoal)]',
  },
  world: {
    active: 'bg-[color:var(--ntn-tint-peach)] border-[color:var(--ntn-brand-orange-deep)]',
    idle: 'bg-[color:var(--ntn-canvas)] border-[color:var(--ntn-hairline)]',
    label: 'text-[color:var(--ntn-brand-orange-deep)]',
  },
};

/* Card kind chip：颜色和它最常出现的 lane 对齐，
   避免「card 颜色 vs lane 颜色」打架。 */
const CARD_KIND_STYLE: Record<CardKind, { chip: string; bar: string }> = {
  user_input: {
    chip: 'bg-[color:var(--ntn-canvas)] text-[color:var(--ntn-brand-purple-800)]',
    bar: 'border-[color:var(--ntn-primary)]',
  },
  tool_use: {
    chip: 'bg-[color:var(--ntn-canvas)] text-[color:var(--ntn-link-blue-pressed)]',
    bar: 'border-[color:var(--ntn-link-blue-pressed)]',
  },
  tool_result: {
    chip: 'bg-[color:var(--ntn-canvas)] text-[color:var(--ntn-brand-orange-deep)]',
    bar: 'border-[color:var(--ntn-brand-orange-deep)]',
  },
  final: {
    chip: 'bg-[color:var(--ntn-canvas)] text-[color:var(--ntn-success)]',
    bar: 'border-[color:var(--ntn-success)]',
  },
};

const GATE_STATE_STYLE: Record<GateState, { chip: string; icon: React.ComponentType<{ size?: number }> }> = {
  open: {
    chip: 'bg-[color:var(--ntn-tint-mint)] text-[color:var(--ntn-success)]',
    icon: Check,
  },
  blocked: {
    chip: 'bg-[color:var(--ntn-tint-rose)] text-[color:var(--ntn-error)]',
    icon: X,
  },
  ask: {
    chip: 'bg-[color:var(--ntn-tint-yellow)] text-[color:var(--ntn-brand-orange-deep)]',
    icon: HelpCircle,
  },
};

const LANE_ICON: Record<LaneId, React.ComponentType<{ size?: number }>> = {
  context: Inbox,
  model: Cpu,
  gate: ShieldCheck,
  world: Globe2,
};

const LANE_ORDER: LaneId[] = ['context', 'model', 'gate', 'world'];

/* ---------------- Helpers ---------------- */

function resolveMoviePath(path: string, lang: 'zh' | 'en'): string {
  if (path.endsWith('.json')) return path.replace(/\.json$/, `.${lang}.json`);
  return path;
}

/* ledger 是「追加只增」的。给定当前帧索引，把 0..idx 之间所有有 delta 的帧聚合出来。
   这样读者点到第 N 帧，看到的 ledger 就是这个 movie 到第 N 帧为止留下的全部痕迹。 */
function accumulateLedger(frames: MovieFrame[], idx: number): Array<{ step: number; delta: string }> {
  const out: Array<{ step: number; delta: string }> = [];
  for (let i = 0; i <= idx; i += 1) {
    const f = frames[i];
    if (f?.ledger?.delta) out.push({ step: f.step, delta: f.ledger.delta });
  }
  return out;
}

/* ---------------- Sub-components ---------------- */

function ActionCard({
  card,
  copy,
}: {
  card: NonNullable<MovieFrame['card']>;
  copy: typeof COPY['zh']['movie'];
}) {
  const style = CARD_KIND_STYLE[card.kind];
  return (
    <div
      className={[
        'ntn-anim-in ntn-shadow-2 bg-[color:var(--ntn-canvas)]',
        'border-l-2 rounded-[var(--ntn-rounded-sm)]',
        'px-2.5 py-2',
        style.bar,
      ].join(' ')}
    >
      <span
        className={[
          'inline-flex items-center px-1.5 py-0.5 rounded-[var(--ntn-rounded-xs)]',
          'text-[10px] font-semibold uppercase tracking-wider',
          style.chip,
        ].join(' ')}
      >
        {copy.cardKinds[card.kind]}
      </span>
      <p className="mt-1.5 text-[11.5px] font-[var(--ntn-font-mono)] leading-snug text-[color:var(--ntn-ink-deep)] break-words">
        {card.label}
      </p>
    </div>
  );
}

function GateBlock({
  gate,
  copy,
}: {
  gate: NonNullable<MovieFrame['gate']>;
  copy: typeof COPY['zh']['movie'];
}) {
  const style = GATE_STATE_STYLE[gate.state];
  const Icon = style.icon;
  return (
    <div className="ntn-anim-in bg-[color:var(--ntn-canvas)] border border-[color:var(--ntn-hairline)] rounded-[var(--ntn-rounded-sm)] px-2.5 py-2">
      <p className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-[color:var(--ntn-stone)]">
        {gate.rule}
      </p>
      {gate.target && (
        <p className="mt-1 text-[11.5px] font-[var(--ntn-font-mono)] text-[color:var(--ntn-ink-deep)] truncate">
          → {gate.target}
        </p>
      )}
      <span
        className={[
          'mt-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[var(--ntn-rounded-xs)]',
          'text-[10px] font-semibold uppercase tracking-wider',
          style.chip,
        ].join(' ')}
      >
        <Icon size={10} />
        {copy.gateStates[gate.state]}
      </span>
    </div>
  );
}

function Lane({
  id,
  name,
  isActive,
  card,
  gate,
  copy,
  onClick,
}: {
  id: LaneId;
  name: string;
  isActive: boolean;
  card?: MovieFrame['card'];
  gate?: MovieFrame['gate'];
  copy: typeof COPY['zh']['movie'];
  onClick: () => void;
}) {
  const tint = LANE_TINT[id];
  const Icon = LANE_ICON[id];
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        'group relative text-left rounded-[var(--ntn-rounded-md)] border transition-colors',
        'p-3 min-h-[180px] flex flex-col gap-2',
        isActive ? tint.active : tint.idle,
        isActive ? 'cursor-default' : 'hover:bg-[color:var(--ntn-surface-soft)]',
      ].join(' ')}
    >
      <div className="flex items-center gap-1.5">
        <Icon size={12} />
        <span
          className={[
            'text-[10px] font-semibold uppercase tracking-[0.14em]',
            isActive ? tint.label : 'text-[color:var(--ntn-stone)]',
          ].join(' ')}
        >
          {name}
        </span>
      </div>

      <div className="flex-1 min-h-0 flex flex-col justify-center">
        {isActive && card && <ActionCard card={card} copy={copy} />}
        {isActive && gate && <GateBlock gate={gate} copy={copy} />}
        {!isActive && (
          <span className="block h-[3px] w-6 rounded-full bg-[color:var(--ntn-hairline-strong)] opacity-40" />
        )}
      </div>
    </button>
  );
}

function LaneSeparator() {
  return (
    <div className="hidden md:flex items-center justify-center text-[color:var(--ntn-stone)]">
      <ArrowRight size={14} />
    </div>
  );
}

function Ledger({
  entries,
  label,
  empty,
}: {
  entries: Array<{ step: number; delta: string }>;
  label: string;
  empty: string;
}) {
  return (
    <div className="border-t border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-tint-mint)] px-4 py-3">
      <div className="flex items-center gap-1.5 mb-1.5">
        <Database size={12} className="text-[color:var(--ntn-success)]" />
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[color:var(--ntn-success)]">
          {label}
        </span>
      </div>
      {entries.length === 0 ? (
        <p className="text-[12px] text-[color:var(--ntn-slate)]">{empty}</p>
      ) : (
        <ul className="space-y-0.5 max-h-[72px] overflow-y-auto ntn-scroll">
          {entries.map((e) => (
            <li
              key={`${e.step}-${e.delta}`}
              className="text-[11.5px] font-[var(--ntn-font-mono)] text-[color:var(--ntn-ink-deep)] truncate"
            >
              <span className="text-[color:var(--ntn-stone)]">{String(e.step).padStart(2, '0')}</span>
              <span className="mx-1.5 text-[color:var(--ntn-stone)]">│</span>
              {e.delta}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------------- Main ---------------- */

export default function HarnessMovie({ moviePath }: HarnessMovieProps) {
  const pathname = usePathname();
  const lang = useMemo(() => localeFromPathname(pathname), [pathname]);
  const copy = COPY[lang].movie;

  const [movie, setMovie] = useState<MovieData | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentStep, setCurrentStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);

  useEffect(() => {
    const path = resolveMoviePath(moviePath, lang);
    /* 失败回落到无后缀路径，保留和 AgentFlowPlayer 一致的容错。 */
    fetch(path)
      .then((r) => (r.ok ? r.json() : fetch(moviePath).then((rr) => rr.json())))
      .then((data: MovieData) => {
        setMovie(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [moviePath, lang]);

  useEffect(() => {
    if (!isPlaying || !movie) return;
    const timer = setInterval(() => {
      setCurrentStep((prev) => {
        if (prev >= movie.frames.length - 1) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, 2400);
    return () => clearInterval(timer);
  }, [isPlaying, movie]);

  if (loading || !movie) {
    return (
      <div className="my-6 ntn-card-soft h-72 flex items-center justify-center text-[13px] text-[color:var(--ntn-slate)]">
        loading run trace…
      </div>
    );
  }

  const frame = movie.frames[currentStep];
  const ledgerEntries = accumulateLedger(movie.frames, currentStep);

  return (
    <div className="my-8">
      {/* ===== 主卡 ===== */}
      <div className="ntn-card overflow-hidden">
        {/* Header */}
        <div className="px-4 py-3 border-b border-[color:var(--ntn-hairline)] flex items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[color:var(--ntn-primary-deep)]">
              {copy.sectionLabel}
            </div>
            <div className="mt-0.5 flex items-baseline gap-2 min-w-0">
              <span className="text-[14px] font-semibold text-[color:var(--ntn-ink-deep)] truncate">
                {movie.title}
              </span>
              <span className="text-[12px] text-[color:var(--ntn-slate)] truncate">
                {copy.taskLabel}: {movie.task}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={() => {
                setIsPlaying(false);
                setCurrentStep((p) => Math.max(0, p - 1));
              }}
              disabled={currentStep === 0}
              aria-label={copy.prev}
              className="p-1.5 rounded-[var(--ntn-rounded-sm)] text-[color:var(--ntn-slate)] hover:bg-[color:var(--ntn-surface)] hover:text-[color:var(--ntn-ink-deep)] disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
            >
              <ChevronLeft size={14} />
            </button>
            <button
              type="button"
              onClick={() => setIsPlaying(!isPlaying)}
              className={[
                'flex items-center gap-1 px-2.5 py-1 rounded-[var(--ntn-rounded-sm)] text-[12px] font-semibold transition-colors',
                isPlaying
                  ? 'border border-[color:var(--ntn-hairline-strong)] text-[color:var(--ntn-ink-deep)] bg-[color:var(--ntn-canvas)]'
                  : 'bg-[color:var(--ntn-primary)] text-[color:var(--ntn-on-primary)] hover:bg-[color:var(--ntn-primary-pressed)]',
              ].join(' ')}
            >
              {isPlaying ? <Pause size={10} fill="currentColor" /> : <Play size={10} fill="currentColor" />}
              {isPlaying ? copy.pause : copy.play}
            </button>
            <button
              type="button"
              onClick={() => {
                setIsPlaying(false);
                setCurrentStep((p) => Math.min(movie.frames.length - 1, p + 1));
              }}
              disabled={currentStep === movie.frames.length - 1}
              aria-label={copy.next}
              className="p-1.5 rounded-[var(--ntn-rounded-sm)] text-[color:var(--ntn-slate)] hover:bg-[color:var(--ntn-surface)] hover:text-[color:var(--ntn-ink-deep)] disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
            >
              <ChevronRight size={14} />
            </button>
            <button
              type="button"
              onClick={() => {
                setIsPlaying(false);
                setCurrentStep(0);
              }}
              aria-label={copy.reset}
              className="p-1.5 rounded-[var(--ntn-rounded-sm)] text-[color:var(--ntn-slate)] hover:bg-[color:var(--ntn-surface)] hover:text-[color:var(--ntn-ink-deep)] transition-colors"
            >
              <RotateCcw size={12} />
            </button>
            <span className="ml-1 text-[11px] font-[var(--ntn-font-mono)] text-[color:var(--ntn-stone)] tabular-nums">
              {copy.frameLabel(currentStep + 1, movie.frames.length)}
            </span>
          </div>
        </div>

        {/* Lanes —— 桌面 4 横列 + 中间小箭头分隔；移动端 2x2 grid */}
        <div className="px-4 py-4 bg-[color:var(--ntn-surface-soft)]">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr] md:gap-1.5">
            {LANE_ORDER.map((id, idx) => (
              <Fragment key={id}>
                <Lane
                  id={id}
                  name={movie.lanes[id] ?? id}
                  isActive={frame.focus === id}
                  card={frame.focus === id ? frame.card : undefined}
                  gate={frame.focus === id ? frame.gate : undefined}
                  copy={copy}
                  onClick={() => {
                    setIsPlaying(false);
                    const i = movie.frames.findIndex((f) => f.focus === id);
                    if (i !== -1) setCurrentStep(i);
                  }}
                />
                {idx < LANE_ORDER.length - 1 && <LaneSeparator />}
              </Fragment>
            ))}
          </div>
        </div>

        {/* Caption */}
        <div className="px-4 py-3 border-t border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)]">
          <p className="text-[13px] leading-relaxed text-[color:var(--ntn-charcoal)]">
            {frame.caption}
          </p>
        </div>

        {/* Ledger */}
        <Ledger entries={ledgerEntries} label={copy.ledgerLabel} empty={copy.emptyLedger} />
      </div>

      {/* ===== Disclosure：MessageInspector ===== */}
      <button
        type="button"
        onClick={() => setInspectorOpen((v) => !v)}
        className="mt-2 inline-flex items-center gap-1.5 text-[12px] font-semibold text-[color:var(--ntn-link-blue)] hover:text-[color:var(--ntn-link-blue-pressed)] transition-colors"
      >
        <ChevronDown
          size={12}
          className={['transition-transform', inspectorOpen ? 'rotate-0' : '-rotate-90'].join(' ')}
        />
        {copy.inspectorToggle(inspectorOpen)}
        <span className="text-[color:var(--ntn-stone)] font-normal font-[var(--ntn-font-mono)]">
          (messages.length = {frame.messages.length}
          {frame.modelResponse ? ' + response' : ''})
        </span>
      </button>

      {inspectorOpen && (
        <div className="mt-2 ntn-anim-in">
          <MessageInspector
            messages={frame.messages}
            modelResponse={frame.modelResponse}
            locale={lang}
          />
        </div>
      )}
    </div>
  );
}
