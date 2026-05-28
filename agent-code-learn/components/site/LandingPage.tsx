"use client";

import { useRef, useState } from 'react';
import Link from 'next/link';
import {
  Archive,
  ArrowRight,
  Cpu,
  Database,
  FilePen,
  GitFork,
  Globe,
  ListChecks,
  LockKeyhole,
  Network,
  Play,
  Plug,
  Sparkles,
  Terminal,
  Users,
  Webhook,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { useGSAP } from '@gsap/react';
import { localizedHref } from '@/lib/href';
import type { Locale } from '@/lib/i18n';
import type { LessonMeta } from '@/lib/lessons';

interface LandingPageProps {
  locale: Locale;
  lessons: LessonMeta[];
}

interface LandingCopy {
  hero: {
    eyebrow: string;
    titlePrefix: string;
    titleEmphasis: string;
    titleSuffix: string;
    subtitle: string;
    primaryCta: string;
    secondaryCta: string;
  };
  proof: string[];
  bento: Array<{
    title: string;
    description: string;
    tone: 'lavender' | 'mint' | 'dark' | 'peach' | 'sky';
  }>;
  pathTitle: string;
  pathLead: string;
  motionTitle: string;
  motionWords: string[];
  layers: Array<{
    title: string;
    body: string;
    icon: 'terminal' | 'code' | 'shield' | 'branch';
  }>;
  ctaTitle: string;
  ctaBody: string;
}

const COPY: Record<Locale, LandingCopy> = {
  zh: {
    hero: {
      eyebrow: 'buildcc.dev',
      titlePrefix: '14 天从零手搓',
      titleEmphasis: 'Code Agent',
      titleSuffix: 'CLI',
      subtitle:
        '不是提示词合集，而是一步步写出 harness：CLI 运行时、真实模型、工具调用、权限、会话记忆和上下文反馈循环。',
      primaryCta: '开始 Day 1',
      secondaryCta: '查看课程路线',
    },
    proof: ['Python + uv 可跑', '真实 Anthropic 工具协议', '14 天渐进式快照'],
    bento: [
      {
        title: '每天都是能跑的小版本',
        description:
          '从空目录开始，每一版都能 `uv run agent-code` 验证，先看到问题，再加下一层 harness。',
        tone: 'lavender',
      },
      {
        title: '学的是 Agent 外壳',
        description:
          '把模型变成代码 Agent 的关键不在 prompt，而在工具边界、权限门禁、上下文和执行反馈。',
        tone: 'dark',
      },
      {
        title: '文档、diff、回放一起看',
        description:
          '教程正文配合代码 diff、终端回放和 Agent Loop 图，让抽象流程变成可检查的工程步骤。',
        tone: 'mint',
      },
      {
        title: '前 7 天做单 Agent CLI',
        description:
          'Hello Agent、真实模型、文件与 Web 工具、安全编辑、Bash 权限、会话记忆、Slash 与 Hooks。',
        tone: 'peach',
      },
      {
        title: '后 7 天升级成完整 harness',
        description:
          'Plan Mode、Skills、Subagents、Context Compact、Coordinator、Worktree 和工具作者指南。',
        tone: 'sky',
      },
    ],
    pathTitle: '从一条命令，长成一个能工作的 Agent',
    pathLead:
      '路线不是先讲概念再写代码，而是让你每天带着一个真实痛点推进：为什么需要 provider、为什么工具要有协议、为什么编辑要先做 diff。',
    motionTitle: '把黑盒 Agent 拆成可维护的工程层',
    motionWords:
      '一个 code agent 的可靠性，来自模型之外的系统：消息账本、工具 schema、权限决策、执行观察、会话恢复和可解释的失败路径。'.split(
        '',
      ),
    layers: [
      {
        title: 'CLI Runtime',
        body: '解析命令、cwd、配置和输出，把一次用户输入变成可追踪的 run。',
        icon: 'terminal',
      },
      {
        title: 'Tool Protocol',
        body: '把 read_file、web_fetch、bash 等能力声明成模型可调用、harness 可验证的工具。',
        icon: 'code',
      },
      {
        title: 'Permission Gates',
        body: '在危险动作前让系统停下来：展示 diff、询问确认、记录拒绝原因。',
        icon: 'shield',
      },
      {
        title: 'Session Memory',
        body: '把上下文写入可恢复的日志，让第二次运行知道上一次发生了什么。',
        icon: 'branch',
      },
    ],
    ctaTitle: '从 buildcc.dev 开始，把 Agent 真的造出来。',
    ctaBody:
      '第一天只做一个能回应的 CLI。十四天后，你会拥有一个能读代码、改文件、跑命令、保存会话的教学版 Code Agent。',
  },
  en: {
    hero: {
      eyebrow: 'buildcc.dev',
      titlePrefix: 'Build a',
      titleEmphasis: 'Code Agent',
      titleSuffix: 'CLI in 14 days',
      subtitle:
        'Not a prompt collection. You will build the harness around the model: runtime, tool calls, permissions, session memory, and feedback loops.',
      primaryCta: 'Start Day 1',
      secondaryCta: 'View curriculum',
    },
    proof: ['Runnable Python + uv', 'Real Anthropic tool protocol', '14 incremental snapshots'],
    bento: [
      {
        title: 'Every day ships a runnable version',
        description:
          'Start from an empty folder, verify each step with `uv run agent-code`, then add the next harness layer.',
        tone: 'lavender',
      },
      {
        title: 'The lesson is the harness',
        description:
          'A code agent is not just prompts. It is tool boundaries, permission gates, context, execution, and feedback.',
        tone: 'dark',
      },
      {
        title: 'Docs, diffs, and replays together',
        description:
          'Each lesson pairs prose with diffs, terminal traces, and Agent Loop diagrams so the system stays inspectable.',
        tone: 'mint',
      },
      {
        title: 'The first 7 days build one agent',
        description:
          'Hello Agent, real model calls, file and web tools, safe edits, Bash permission, session memory, slash commands.',
        tone: 'peach',
      },
      {
        title: 'The next 7 days upgrade the harness',
        description:
          'Plan Mode, Skills, Subagents, Context Compact, Coordinator, Worktree isolation, and tool authoring.',
        tone: 'sky',
      },
    ],
    pathTitle: 'From one command to a working coding agent',
    pathLead:
      'The path is pain-driven: why providers exist, why tools need protocol shapes, and why file edits need diffs before writes.',
    motionTitle: 'Make the agent black box maintainable',
    motionWords:
      'A reliable code agent lives outside the model: message ledgers, tool schemas, permission decisions, execution observations, resumable sessions, and explainable failure paths.'.split(
        ' ',
      ),
    layers: [
      {
        title: 'CLI Runtime',
        body: 'Parse commands, cwd, config, and output so every user request becomes a traceable run.',
        icon: 'terminal',
      },
      {
        title: 'Tool Protocol',
        body: 'Expose read_file, web_fetch, bash, and more as callable tools the harness can verify.',
        icon: 'code',
      },
      {
        title: 'Permission Gates',
        body: 'Pause before risky actions: show diffs, ask for approval, and record blocked decisions.',
        icon: 'shield',
      },
      {
        title: 'Session Memory',
        body: 'Persist context into resumable logs so the next run knows what happened last time.',
        icon: 'branch',
      },
    ],
    ctaTitle: 'Start at buildcc.dev and build the agent for real.',
    ctaBody:
      'Day 1 is a tiny CLI that answers once. By Day 14, it can read code, edit files, run commands, and resume sessions.',
  },
};

/* Bento 卡片的「图」必须服务于卡片主题，避免用占位图。
   这里手画 5 个轻量 SVG，颜色全部继承 currentColor，
   外面用 text-[color:...] 控制是浅卡还是深卡的笔触。 */
function BentoArt({ index }: { index: number }) {
  const stroke = 'currentColor';
  switch (index) {
    case 0:
      // v1 → v2 → v3 渐进式可跑迭代
      return (
        <svg viewBox="0 0 320 112" className="h-full w-full" aria-hidden>
          {['v1', 'v2', 'v3'].map((label, i) => (
            <g key={label} transform={`translate(${10 + i * 100}, 28)`}>
              <rect width="84" height="56" rx="14" fill="none" stroke={stroke} strokeOpacity="0.7" strokeWidth="1.4" />
              <text x="14" y="24" fontFamily="ui-monospace, SFMono-Regular, monospace" fontSize="11" fill={stroke} opacity="0.55">$ run</text>
              <text x="14" y="44" fontFamily="ui-monospace, SFMono-Regular, monospace" fontSize="16" fontWeight="600" fill={stroke}>{label}</text>
            </g>
          ))}
          {[0, 1].map((i) => (
            <path
              key={i}
              d={`M${94 + i * 100} 56 L${108 + i * 100} 56`}
              stroke={stroke}
              strokeOpacity="0.6"
              strokeWidth="1.5"
              markerEnd="url(#bento0-arrow)"
              fill="none"
            />
          ))}
          <defs>
            <marker id="bento0-arrow" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0 0 L8 4 L0 8 Z" fill={stroke} opacity="0.6" />
            </marker>
          </defs>
        </svg>
      );
    case 1:
      // model 外面一层层 harness 包裹
      return (
        <svg viewBox="0 0 320 112" className="h-full w-full" aria-hidden>
          {[0, 1, 2].map((i) => (
            <rect
              key={i}
              x={40 + i * 18}
              y={6 + i * 14}
              width={240 - i * 36}
              height={100 - i * 28}
              rx={14 - i * 3}
              fill="none"
              stroke={stroke}
              strokeOpacity={0.35 + i * 0.18}
              strokeWidth="1.4"
            />
          ))}
          <rect x="136" y="44" width="48" height="24" rx="6" fill={stroke} opacity="0.9" />
          <text x="160" y="61" textAnchor="middle" fontFamily="ui-monospace, SFMono-Regular, monospace" fontSize="11" fontWeight="600" fill="#0F0F12">
            model
          </text>
          <text x="160" y="98" textAnchor="middle" fontFamily="ui-monospace, SFMono-Regular, monospace" fontSize="9" fill={stroke} opacity="0.55" letterSpacing="2">
            tools · permissions · context
          </text>
        </svg>
      );
    case 2:
      // 文档 + diff + 回放
      return (
        <svg viewBox="0 0 320 112" className="h-full w-full" aria-hidden>
          <g transform="translate(28, 12)">
            <rect width="120" height="88" rx="10" fill="none" stroke={stroke} strokeOpacity="0.55" strokeWidth="1.4" />
            <line x1="12" y1="22" x2="92" y2="22" stroke={stroke} strokeOpacity="0.7" strokeWidth="3" strokeLinecap="round" />
            <line x1="12" y1="36" x2="74" y2="36" stroke={stroke} strokeOpacity="0.35" strokeWidth="2" strokeLinecap="round" />
            <line x1="12" y1="48" x2="82" y2="48" stroke={stroke} strokeOpacity="0.35" strokeWidth="2" strokeLinecap="round" />
            <line x1="12" y1="60" x2="60" y2="60" stroke={stroke} strokeOpacity="0.35" strokeWidth="2" strokeLinecap="round" />
            <line x1="12" y1="72" x2="68" y2="72" stroke={stroke} strokeOpacity="0.35" strokeWidth="2" strokeLinecap="round" />
          </g>
          <g transform="translate(168, 12)">
            <rect width="120" height="40" rx="8" fill="none" stroke={stroke} strokeOpacity="0.55" strokeWidth="1.4" />
            <text x="10" y="17" fontFamily="ui-monospace, SFMono-Regular, monospace" fontSize="10" fill={stroke} opacity="0.85">- hello</text>
            <text x="10" y="32" fontFamily="ui-monospace, SFMono-Regular, monospace" fontSize="10" fill={stroke} opacity="0.85">+ bonjour</text>
          </g>
          <g transform="translate(168, 60)">
            <rect width="120" height="40" rx="8" fill="none" stroke={stroke} strokeOpacity="0.55" strokeWidth="1.4" />
            <polygon points="14,12 14,28 30,20" fill={stroke} opacity="0.85" />
            <line x1="42" y1="20" x2="108" y2="20" stroke={stroke} strokeOpacity="0.45" strokeWidth="2" strokeLinecap="round" />
          </g>
        </svg>
      );
    case 3:
      // 单 Agent CLI：1 个圆 + 命令行
      return (
        <svg viewBox="0 0 320 112" className="h-full w-full" aria-hidden>
          <circle cx="60" cy="56" r="32" fill="none" stroke={stroke} strokeOpacity="0.55" strokeWidth="1.4" />
          <circle cx="60" cy="56" r="14" fill={stroke} opacity="0.9" />
          <text x="60" y="60" textAnchor="middle" fontFamily="ui-monospace, SFMono-Regular, monospace" fontSize="11" fontWeight="600" fill="#0F0F12">A</text>
          <g transform="translate(108, 26)">
            <rect width="180" height="60" rx="10" fill="none" stroke={stroke} strokeOpacity="0.55" strokeWidth="1.4" />
            <text x="14" y="24" fontFamily="ui-monospace, SFMono-Regular, monospace" fontSize="12" fill={stroke} opacity="0.85">$ agent-code &quot;…&quot;</text>
            <text x="14" y="44" fontFamily="ui-monospace, SFMono-Regular, monospace" fontSize="11" fill={stroke} opacity="0.55">tool_use → result → final</text>
          </g>
        </svg>
      );
    case 4:
    default:
      // 完整 harness：1 个 coordinator 连接 4 个子 Agent
      return (
        <svg viewBox="0 0 320 112" className="h-full w-full" aria-hidden>
          {[
            { x: 36, y: 24 },
            { x: 36, y: 88 },
            { x: 284, y: 24 },
            { x: 284, y: 88 },
          ].map((p, i) => (
            <line
              key={i}
              x1="160"
              y1="56"
              x2={p.x}
              y2={p.y}
              stroke={stroke}
              strokeOpacity="0.4"
              strokeWidth="1.4"
              strokeDasharray="3 4"
            />
          ))}
          {[
            { x: 36, y: 24 },
            { x: 36, y: 88 },
            { x: 284, y: 24 },
            { x: 284, y: 88 },
          ].map((p, i) => (
            <circle
              key={i}
              cx={p.x}
              cy={p.y}
              r="14"
              fill="none"
              stroke={stroke}
              strokeOpacity="0.7"
              strokeWidth="1.4"
            />
          ))}
          <circle cx="160" cy="56" r="22" fill={stroke} opacity="0.9" />
          <text x="160" y="60" textAnchor="middle" fontFamily="ui-monospace, SFMono-Regular, monospace" fontSize="11" fontWeight="600" fill="#0F0F12">
            coord
          </text>
        </svg>
      );
  }
}

/* 14 节课每节一个独立 SVG 图标，落地页路线图按 slug 直接取。
   未匹配到的 slug 退回 Sparkles，避免 Day 命名改动时白图标。 */
const LESSON_ICONS: Record<string, LucideIcon> = {
  'day-01-hello-agent': Sparkles,
  'day-02-real-model-tool-calling': Cpu,
  'day-03-file-and-web-tools': Globe,
  'day-04-safe-edit': FilePen,
  'day-05-bash-permissions': Terminal,
  'day-06-session-memory': Database,
  'day-07-slash-hooks': Webhook,
  'day-08-todo-plan-mode': ListChecks,
  'day-09-skills': Wrench,
  'day-10-subagents': Users,
  'day-11-context-compact': Archive,
  'day-12-agent-coordinator': Network,
  'day-13-worktree-and-final-demo': GitFork,
  'day-14-mcp-and-tool-search': Plug,
};

const toneClass: Record<LandingCopy['bento'][number]['tone'], string> = {
  lavender: 'bg-[color:var(--ntn-tint-lavender)]',
  mint: 'bg-[color:var(--ntn-tint-mint)]',
  dark: 'bg-[color:var(--ntn-code-bg)] text-[color:var(--ntn-code-fg)]',
  peach: 'bg-[color:var(--ntn-tint-peach)]',
  sky: 'bg-[color:var(--ntn-tint-sky)]',
};

export default function LandingPage({ locale, lessons }: LandingPageProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const copy = COPY[locale];
  const readyLessons = lessons.filter((lesson) => lesson.status === 'ready');
  const firstLessonHref = localizedHref('/docs/day-01-hello-agent', locale);
  const [lockedLesson, setLockedLesson] = useState<LessonMeta | null>(null);
  const curriculumGroups = [
    {
      title: locale === 'zh' ? 'Get started' : 'Get started',
      description: locale === 'zh' ? '把 CLI 跑起来，接入真实模型。' : 'Bring the CLI online and connect a real model.',
      lessons: lessons.slice(0, 2),
    },
    {
      title: locale === 'zh' ? 'Build' : 'Build',
      description: locale === 'zh' ? '补齐文件、Web、编辑、Bash 和权限。' : 'Add files, web, edits, Bash, and permissions.',
      lessons: lessons.slice(2, 5),
    },
    {
      title: locale === 'zh' ? 'Remember & control' : 'Remember & control',
      description: locale === 'zh' ? '加入会话记忆、Slash 命令和 hooks。' : 'Add session memory, slash commands, and hooks.',
      lessons: lessons.slice(5, 7),
    },
    {
      title: locale === 'zh' ? 'Scale the harness' : 'Scale the harness',
      description: locale === 'zh' ? '把单 Agent 升级成可规划、可扩展的系统。' : 'Upgrade one agent into a planful, extensible system.',
      lessons: lessons.slice(7, 11),
    },
    {
      title: locale === 'zh' ? 'Ship & extend' : 'Ship & extend',
      description: locale === 'zh' ? '协调多 Agent、隔离工作区，并写出工具作者指南。' : 'Coordinate agents, isolate worktrees, and author tools.',
      lessons: lessons.slice(11, 14),
    },
  ];

  useGSAP(
    () => {
      gsap.registerPlugin(ScrollTrigger);

      const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (prefersReducedMotion) return;

      gsap.from('[data-hero-word]', {
        y: 42,
        opacity: 0,
        duration: 0.9,
        ease: 'power3.out',
        stagger: 0.08,
      });

      gsap.from('[data-hero-panel]', {
        y: 54,
        opacity: 0,
        scale: 0.96,
        duration: 1,
        ease: 'power3.out',
        delay: 0.15,
      });

      gsap.to('[data-reveal-word]', {
        opacity: 1,
        y: 0,
        stagger: 0.025,
        ease: 'none',
        scrollTrigger: {
          trigger: '[data-reveal-copy]',
          start: 'top 78%',
          end: 'bottom 48%',
          scrub: true,
        },
      });

      gsap.utils.toArray<HTMLElement>('[data-stack-card]').forEach((card, index) => {
        gsap.from(card, {
          y: 72 + index * 12,
          opacity: 0.3,
          scale: 0.94,
          scrollTrigger: {
            trigger: card,
            start: 'top 88%',
            end: 'top 48%',
            scrub: true,
          },
        });
      });

      gsap.utils.toArray<HTMLElement>('[data-motion-card]').forEach((card) => {
        gsap.fromTo(
          card,
          { scale: 0.92, opacity: 0.62 },
          {
            scale: 1,
            opacity: 1,
            scrollTrigger: {
              trigger: card,
              start: 'top 86%',
              end: 'top 45%',
              scrub: true,
            },
          },
        );
      });
    },
    { scope: rootRef },
  );

  return (
    <main
      ref={rootRef}
      className="overflow-x-hidden w-full max-w-full bg-[color:var(--ntn-canvas)] text-[color:var(--ntn-ink)]"
    >
      <section className="relative min-h-[calc(100vh-64px)] px-6 py-20 md:py-28">
        <div className="absolute inset-0 -z-10 overflow-hidden">
          <div className="absolute left-[-12%] top-[-18%] h-[520px] w-[520px] rounded-full bg-[color:var(--ntn-tint-lavender)] blur-3xl opacity-70" />
          <div className="absolute right-[-14%] top-[18%] h-[460px] w-[460px] rounded-full bg-[color:var(--ntn-tint-peach)] blur-3xl opacity-70" />
          <div className="absolute inset-x-0 bottom-0 h-44 bg-gradient-to-t from-[color:var(--ntn-canvas)] to-transparent" />
        </div>

        <div className="mx-auto grid max-w-[1400px] grid-cols-1 items-center gap-14 lg:grid-cols-[minmax(0,1.05fr)_minmax(420px,0.95fr)]">
          <div>
            <h1 className="max-w-6xl text-[clamp(3rem,6vw,6.25rem)] font-semibold leading-[0.95] tracking-[-0.075em] text-[color:var(--ntn-ink-deep)]">
              <span data-hero-word className="block">
                {copy.hero.titlePrefix}
              </span>
              <span data-hero-word className="block">
                {copy.hero.titleEmphasis}
              </span>
              <span data-hero-word className="block">
                {copy.hero.titleSuffix}
              </span>
            </h1>

            <p className="mt-7 max-w-2xl text-[18px] leading-8 text-[color:var(--ntn-slate)] md:text-[20px]">
              {copy.hero.subtitle}
            </p>

            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <Link
                href={firstLessonHref}
                className="group inline-flex h-12 items-center justify-center gap-2 rounded-[var(--ntn-rounded-md)] bg-[#0F0F12] px-6 text-[15px] font-semibold shadow-[var(--ntn-shadow-2)] transition-transform hover:-translate-y-0.5"
                style={{ color: '#FFFFFF' }}
              >
                {copy.hero.primaryCta}
                <ArrowRight size={17} strokeWidth={2} className="transition-transform group-hover:translate-x-0.5" />
              </Link>
              <a
                href="#curriculum"
                className="inline-flex h-12 items-center justify-center rounded-[var(--ntn-rounded-md)] border border-[color:var(--ntn-hairline-strong)] bg-[color:var(--ntn-canvas)] px-6 text-[15px] font-semibold text-[color:var(--ntn-ink-deep)] transition-colors hover:bg-[color:var(--ntn-surface)]"
              >
                {copy.hero.secondaryCta}
              </a>
            </div>

            <div className="mt-10 grid max-w-2xl grid-cols-1 gap-2 sm:grid-cols-3">
              {copy.proof.map((item) => (
                <div
                  key={item}
                  className="rounded-[var(--ntn-rounded-lg)] border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)]/72 px-4 py-3 text-[13px] font-medium text-[color:var(--ntn-charcoal)] shadow-[var(--ntn-shadow-1)]"
                >
                  {item}
                </div>
              ))}
            </div>
          </div>

          <div
            data-hero-panel
            className="relative rounded-[var(--ntn-rounded-xxxl)] border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-surface-soft)] p-3 shadow-[var(--ntn-shadow-3)]"
          >
            <div className="overflow-hidden rounded-[calc(var(--ntn-rounded-xxxl)-8px)] border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)]">
              <div className="flex items-center justify-between border-b border-[color:var(--ntn-hairline)] px-5 py-4">
                <div className="flex items-center gap-2">
                  <span className="h-3 w-3 rounded-full bg-[color:var(--ntn-error)]" />
                  <span className="h-3 w-3 rounded-full bg-[color:var(--ntn-warning)]" />
                  <span className="h-3 w-3 rounded-full bg-[color:var(--ntn-success)]" />
                </div>
                <span className="font-[var(--ntn-font-mono)] text-[11px] text-[color:var(--ntn-stone)]">
                  agent-code run
                </span>
              </div>

              <div className="grid gap-3 p-5">
                <div className="rounded-[var(--ntn-rounded-xl)] bg-[color:var(--ntn-code-bg)] p-5 text-[color:var(--ntn-code-fg)]">
                  <div className="mb-6 flex items-center gap-2 text-[12px] text-white/55">
                    <Terminal size={15} />
                    <span className="font-[var(--ntn-font-mono)]">uv run agent-code</span>
                  </div>
                  <div className="space-y-3 font-[var(--ntn-font-mono)] text-[13px] leading-6">
                    <p className="text-white/72">$ agent-code "read README, explain the CLI"</p>
                    <p className="text-[color:var(--ntn-brand-purple-300)]">tool_use: read_file</p>
                    <p className="text-[color:var(--ntn-brand-teal)]">observation: 42 lines</p>
                    <p className="text-white">final: here is the harness boundary...</p>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-3">
                  {[
                    ['14', locale === 'zh' ? '天路线' : 'days'],
                    [String(readyLessons.length), locale === 'zh' ? '章已上线' : 'ready'],
                    ['1', locale === 'zh' ? '个 CLI' : 'CLI'],
                  ].map(([value, label]) => (
                    <div
                      key={label}
                      className="rounded-[var(--ntn-rounded-lg)] bg-[color:var(--ntn-tint-lavender)] p-4"
                    >
                      <div className="text-3xl font-semibold tracking-[-0.06em] text-[color:var(--ntn-ink-deep)]">
                        {value}
                      </div>
                      <div className="mt-1 text-[12px] text-[color:var(--ntn-slate)]">{label}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="px-6 py-28 md:py-40">
        <div className="mx-auto max-w-[1400px]">
          <div className="grid-flow-dense grid grid-cols-1 gap-4 md:grid-cols-12">
            {copy.bento.map((card, index) => {
              const span =
                index === 0
                  ? 'md:col-span-5 md:row-span-2'
                  : index === 1
                    ? 'md:col-span-4 md:row-span-2'
                    : index === 2
                      ? 'md:col-span-3 md:row-span-2'
                      : index === 3
                        ? 'md:col-span-7'
                        : 'md:col-span-5';

              return (
                <article
                  key={card.title}
                  data-motion-card
                  className={[
                    span,
                    toneClass[card.tone],
                    'group overflow-hidden rounded-[var(--ntn-rounded-xxl)] border border-[color:var(--ntn-hairline)] p-7 shadow-[var(--ntn-shadow-1)] transition-transform duration-700 ease-out hover:-translate-y-1',
                  ].join(' ')}
                >
                  <div
                    className={[
                      'mb-8 h-28 overflow-hidden rounded-[var(--ntn-rounded-xl)] p-4 transition-transform duration-700 ease-out group-hover:-translate-y-0.5',
                      card.tone === 'dark'
                        ? 'bg-white/[0.06] text-white/85'
                        : 'bg-[color:var(--ntn-canvas)]/55 text-[color:var(--ntn-ink-deep)]/80',
                    ].join(' ')}
                  >
                    <BentoArt index={index} />
                  </div>
                  <h2
                    className={[
                      'max-w-[22rem] text-[28px] font-semibold leading-none tracking-[-0.05em]',
                      card.tone === 'dark' ? 'text-white' : 'text-[color:var(--ntn-ink-deep)]',
                    ].join(' ')}
                  >
                    {card.title}
                  </h2>
                  <p
                    className={[
                      'mt-4 max-w-xl text-[15px] leading-7',
                      card.tone === 'dark' ? 'text-white/72' : 'text-[color:var(--ntn-slate)]',
                    ].join(' ')}
                  >
                    {card.description}
                  </p>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      <section id="curriculum" className="px-6 py-28 md:py-40">
        <div className="mx-auto max-w-[1400px]">
          <div className="mb-12 max-w-4xl">
            <h2 className="text-[clamp(2.5rem,5vw,5.5rem)] font-semibold leading-[0.95] tracking-[-0.07em] text-[color:var(--ntn-ink-deep)]">
              {copy.pathTitle}
            </h2>
            <p className="mt-6 max-w-3xl text-[18px] leading-8 text-[color:var(--ntn-slate)]">
              {copy.pathLead}
            </p>
          </div>

          <div className="relative space-y-10">
            <div className="absolute bottom-2 left-5 top-5 hidden w-px bg-[color:var(--ntn-hairline-strong)] md:block" />
            {curriculumGroups.map((group, groupIndex) => (
              <div key={group.title} className="relative grid gap-5 md:grid-cols-[40px_minmax(0,1fr)] md:gap-7">
                <div className="hidden md:block">
                  <div className="relative z-10 flex h-10 w-10 items-center justify-center rounded-full border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)] text-[13px] font-semibold text-[color:var(--ntn-ink-deep)] shadow-[var(--ntn-shadow-1)]">
                    {groupIndex + 1}
                  </div>
                </div>

                <div>
                  <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                      <h3 className="text-[22px] font-semibold tracking-[-0.035em] text-[color:var(--ntn-ink-deep)]">
                        {group.title}
                      </h3>
                      <p className="mt-1 text-[14px] leading-6 text-[color:var(--ntn-slate)]">
                        {group.description}
                      </p>
                    </div>
                    <span className="font-[var(--ntn-font-mono)] text-[11px] text-[color:var(--ntn-stone)]">
                      {group.lessons.length} lessons
                    </span>
                  </div>

                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {group.lessons.map((lesson) => {
                      const isReady = lesson.status === 'ready';
                      const href = isReady ? localizedHref(`/docs/${lesson.slug}`, locale) : undefined;
                      const Icon = LESSON_ICONS[lesson.slug] ?? Sparkles;
                      const label = lesson.dayLabel.replace(/^Day \d+ · /, '');
                      const day = lesson.dayLabel.match(/^Day \d+/)?.[0] ?? '';
                      const card = (
                        <div className="group/card flex min-h-[72px] items-center gap-4 rounded-[var(--ntn-rounded-lg)] border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)] px-4 py-3 shadow-[var(--ntn-shadow-1)] transition-all duration-300 hover:-translate-y-0.5 hover:border-[color:var(--ntn-hairline-strong)] hover:bg-[color:var(--ntn-surface-soft)] hover:shadow-[var(--ntn-shadow-2)]">
                          <span className={[
                            'relative flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--ntn-rounded-md)] border transition-colors duration-300',
                            isReady
                              ? 'border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-tint-lavender)] text-[color:var(--ntn-brand-purple-800)]'
                              : 'border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-surface)] text-[color:var(--ntn-stone)]',
                          ].join(' ')}>
                            <Icon size={16} strokeWidth={1.8} />
                            {!isReady && (
                              <span
                                className="absolute -right-1 -bottom-1 flex h-4 w-4 items-center justify-center rounded-full border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)] text-[color:var(--ntn-stone)] shadow-[var(--ntn-shadow-1)]"
                                aria-hidden
                              >
                                <LockKeyhole size={9} strokeWidth={2} />
                              </span>
                            )}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block font-[var(--ntn-font-mono)] text-[10px] uppercase tracking-[0.14em] text-[color:var(--ntn-stone)]">
                              {day}
                            </span>
                            <span className="mt-0.5 block truncate text-[14px] font-semibold text-[color:var(--ntn-ink-deep)]">
                              {label}
                            </span>
                          </span>
                          {isReady ? (
                            <ArrowRight size={15} className="shrink-0 text-[color:var(--ntn-stone)] transition-transform duration-300 group-hover/card:translate-x-0.5 group-hover/card:text-[color:var(--ntn-primary)]" />
                          ) : (
                            <span className="shrink-0 rounded-full bg-[color:var(--ntn-surface)] px-2 py-1 text-[11px] font-medium text-[color:var(--ntn-stone)]">
                              {locale === 'zh' ? '未解锁' : 'Locked'}
                            </span>
                          )}
                        </div>
                      );

                      return href ? (
                        <Link key={lesson.slug} href={href}>
                          {card}
                        </Link>
                      ) : (
                        <button
                          key={lesson.slug}
                          type="button"
                          onClick={() => setLockedLesson(lesson)}
                          className="w-full text-left"
                        >
                          {card}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="px-6 py-28 md:py-40">
        <div className="mx-auto max-w-[1400px] overflow-hidden rounded-[var(--ntn-rounded-xxxl)] bg-[color:var(--ntn-brand-navy)] px-6 py-16 text-center text-[color:var(--ntn-on-dark)] shadow-[var(--ntn-shadow-3)] md:px-16 md:py-24">
          <div className="mx-auto mb-8 flex h-16 w-16 items-center justify-center rounded-[var(--ntn-rounded-xl)] bg-white/10">
            <Play size={25} fill="currentColor" strokeWidth={1.8} />
          </div>
          <h2 className="mx-auto max-w-5xl text-[clamp(2.75rem,6vw,6.5rem)] font-semibold leading-[0.92] tracking-[-0.075em]">
            {copy.ctaTitle}
          </h2>
          <p className="mx-auto mt-7 max-w-3xl text-[18px] leading-8 text-[color:var(--ntn-on-dark-muted)]">
            {copy.ctaBody}
          </p>
          <div className="mt-10">
            <Link
              href={firstLessonHref}
              className="group inline-flex h-12 items-center justify-center gap-2 rounded-[var(--ntn-rounded-md)] bg-white px-6 text-[15px] font-semibold transition-transform hover:-translate-y-0.5"
              style={{ color: '#0F0F12' }}
            >
              {copy.hero.primaryCta}
              <ArrowRight size={17} strokeWidth={2} className="transition-transform group-hover:translate-x-0.5" />
            </Link>
          </div>
        </div>
      </section>

      {lockedLesson && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-6 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="locked-lesson-title"
          onClick={() => setLockedLesson(null)}
        >
          <div
            className="w-full max-w-md rounded-[var(--ntn-rounded-xxl)] border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)] p-7 text-center shadow-[var(--ntn-shadow-3)]"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-[var(--ntn-rounded-lg)] bg-[color:var(--ntn-tint-lavender)] text-[color:var(--ntn-brand-purple-800)]">
              <LockKeyhole size={20} strokeWidth={1.8} />
            </div>
            <h2 id="locked-lesson-title" className="text-[28px] font-semibold tracking-[-0.05em] text-[color:var(--ntn-ink-deep)]">
              {locale === 'zh' ? '敬请期待' : 'Coming soon'}
            </h2>
            <p className="mt-3 text-[15px] leading-7 text-[color:var(--ntn-slate)]">
              {locale === 'zh'
                ? `${lockedLesson.dayLabel} 还在打磨中，解锁后会补上完整教程、代码 diff 和终端回放。`
                : `${lockedLesson.dayLabel} is still being polished. The full lesson, diffs, and terminal replay will unlock later.`}
            </p>
            <button
              type="button"
              onClick={() => setLockedLesson(null)}
              className="mt-6 inline-flex h-10 items-center justify-center rounded-[var(--ntn-rounded-md)] bg-[#0F0F12] px-5 text-[14px] font-semibold text-white"
            >
              {locale === 'zh' ? '知道了' : 'Got it'}
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
