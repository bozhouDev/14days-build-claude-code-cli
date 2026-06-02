import type { Locale } from './i18n';

/* 文档站左侧 LessonSidebar 用的章节元数据。
   只保留导航必需的字段：slug、stage、dayLabel、status；
   primaryFile / concepts 之前是 landing 的 LessonBreakdownTable 用的，现已下线。 */

export type LessonStatus = 'ready' | 'soon' | 'locked';

export interface LessonMeta {
  slug: string;
  stage: string;
  dayLabel: string;
  status: LessonStatus;
}

export interface LessonGroup {
  stage: string;
  stageTitle: string;
  lessons: LessonMeta[];
}

/* 14 天路线参见仓库根目录 `14days-build-claude-code-cli-plan-python.md`。
   前 7 天（Stage 01）做"能用的单 Agent CLI"；
   后 7 天（Stage 02）把它升级成完整 Claude Code 风格 harness。
   slug 与 `content/docs/` 下的 mdx 文件名一致，未发布的章节先标 locked。 */
export function getLessons(locale: Locale): LessonMeta[] {
  if (locale === 'en') {
    return [
      { slug: 'day-01-hello-agent', stage: 'Stage 01', dayLabel: 'Day 1 · Hello Agent', status: 'ready' },
      { slug: 'day-02-real-model-tool-calling', stage: 'Stage 01', dayLabel: 'Day 2 · Real Model + Tool Calling', status: 'ready' },
      { slug: 'day-03-file-and-web-tools', stage: 'Stage 01', dayLabel: 'Day 3 · File + Web Tools', status: 'ready' },
      { slug: 'day-04-safe-edit', stage: 'Stage 01', dayLabel: 'Day 4 · Safe Edit', status: 'ready' },
      { slug: 'day-05-bash-permissions', stage: 'Stage 01', dayLabel: 'Day 5 · Bash + Permission', status: 'ready' },
      { slug: 'day-06-session-memory', stage: 'Stage 01', dayLabel: 'Day 6 · Session + Memory', status: 'ready' },
      { slug: 'day-07-slash-hooks', stage: 'Stage 01', dayLabel: 'Day 7 · Slash + Hooks + Cron', status: 'ready' },
      { slug: 'day-08-interactive-shell', stage: 'Stage 02', dayLabel: 'Day 8 · Interactive Shell + Plan Mode', status: 'ready' },
      { slug: 'day-09-skills', stage: 'Stage 02', dayLabel: 'Day 9 · Skills', status: 'locked' },
      { slug: 'day-10-subagents', stage: 'Stage 02', dayLabel: 'Day 10 · Subagents', status: 'locked' },
      { slug: 'day-11-context-compact', stage: 'Stage 02', dayLabel: 'Day 11 · Context Compact + Cost', status: 'locked' },
      { slug: 'day-12-agent-coordinator', stage: 'Stage 02', dayLabel: 'Day 12 · Agent Coordinator', status: 'locked' },
      { slug: 'day-13-worktree-and-final-demo', stage: 'Stage 02', dayLabel: 'Day 13 · Worktree + Final Demo', status: 'locked' },
      { slug: 'day-14-mcp-and-tool-search', stage: 'Stage 02', dayLabel: 'Day 14 · MCP + ToolSearch', status: 'locked' },
    ];
  }

  return [
    { slug: 'day-01-hello-agent', stage: 'Stage 01', dayLabel: 'Day 1 · Hello Agent', status: 'ready' },
    { slug: 'day-02-real-model-tool-calling', stage: 'Stage 01', dayLabel: 'Day 2 · 接入真实模型', status: 'ready' },
    { slug: 'day-03-file-and-web-tools', stage: 'Stage 01', dayLabel: 'Day 3 · 文件 + Web 工具', status: 'ready' },
    { slug: 'day-04-safe-edit', stage: 'Stage 01', dayLabel: 'Day 4 · 安全文件编辑', status: 'ready' },
    { slug: 'day-05-bash-permissions', stage: 'Stage 01', dayLabel: 'Day 5 · Bash + 权限', status: 'ready' },
    { slug: 'day-06-session-memory', stage: 'Stage 01', dayLabel: 'Day 6 · 会话 + 记忆', status: 'ready' },
    { slug: 'day-07-slash-hooks', stage: 'Stage 01', dayLabel: 'Day 7 · Slash + Hooks + Cron', status: 'ready' },
    { slug: 'day-08-interactive-shell', stage: 'Stage 02', dayLabel: 'Day 8 · 交互式 Shell + Plan Mode', status: 'ready' },
    { slug: 'day-09-skills', stage: 'Stage 02', dayLabel: 'Day 9 · Skills ', status: 'locked' },
    { slug: 'day-10-subagents', stage: 'Stage 02', dayLabel: 'Day 10 · Subagents 子代理', status: 'locked' },
    { slug: 'day-11-context-compact', stage: 'Stage 02', dayLabel: 'Day 11 · 长上下文压缩 + Cost', status: 'locked' },
    { slug: 'day-12-agent-coordinator', stage: 'Stage 02', dayLabel: 'Day 12 · Agent teams', status: 'locked' },
    { slug: 'day-13-worktree-and-final-demo', stage: 'Stage 02', dayLabel: 'Day 13 · Worktree隔离', status: 'locked' },
    { slug: 'day-14-mcp-and-tool-search', stage: 'Stage 02', dayLabel: 'Day 14 · MCP + ToolSearch', status: 'locked' },
  ];
}

/* 兼容老调用：groups 内每个 stage 的 lessons 现在只是按 stage 字段过滤的视图。 */
export function getLessonGroups(locale: Locale): LessonGroup[] {
  const all = getLessons(locale);
  const stages = Array.from(new Set(all.map((l) => l.stage)));
  return stages.map((stage) => ({
    stage,
    stageTitle: '',
    lessons: all.filter((l) => l.stage === stage),
  }));
}

export function findLessonMeta(slug: string, locale: Locale): LessonMeta | undefined {
  return getLessons(locale).find((l) => l.slug === slug);
}

/* 给文章页 pager 用的相邻章节查找：返回当前 lesson 在 14 天里的位置、上一篇、下一篇。
   prev/next 只看「在课程数组里相邻」，不跳过 locked —— 让 pager 同样能展示 "下一篇还没发布"
   的占位状态，对学生是真实的进度信号；具体能不能点由 pager 组件自己决定。 */
export interface LessonNeighbors {
  index: number; // 1-based, e.g. 4 for Day 4
  total: number; // 总课程数（14）
  prev?: LessonMeta;
  next?: LessonMeta;
  current?: LessonMeta;
}

export function getLessonNeighbors(slug: string, locale: Locale): LessonNeighbors {
  const all = getLessons(locale);
  const idx = all.findIndex((l) => l.slug === slug);
  if (idx === -1) return { index: 0, total: all.length };
  return {
    index: idx + 1,
    total: all.length,
    prev: idx > 0 ? all[idx - 1] : undefined,
    next: idx < all.length - 1 ? all[idx + 1] : undefined,
    current: all[idx],
  };
}
