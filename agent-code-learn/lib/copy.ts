import type { Locale } from './i18n';

/* 站点级固定文案。MDX 正文的翻译走 .zh.mdx / .en.mdx 后缀。
   纯文档站只需要两块：
   - chrome：顶栏 brand + 底栏 smallprint
   - docs  ：左栏目录标题 + 右栏 TOC 标签 + 语言切换无障碍标签 */

export interface ChromeCopy {
  brand: string;
  footerSmallprint: string;
  /* footer 右侧两条出口链接的可见标签。
     repo 指向项目源码（GitHub），start 指向 Day 1（即整站根路径）。 */
  footerLinks: {
    repo: string;
    start: string;
  };
}

export interface DocsCopy {
  sidebarHeading: string;
  sidebarSubheading: string;
  tocLabel: string;
  langSwitcherLabel: string;
  /* 文章页 hero 用：14 天里第 N 天的小指示标签（"Day N / 14"）。 */
  dayOfTotal: (cur: number, total: number) => string;
  /* 文章页底部 prev/next pager。 */
  prevLessonLabel: string;
  nextLessonLabel: string;
  /* 「即将到来」占位用：next 还没发布。 */
  comingSoonLabel: string;
  /* 悬浮回到顶部按钮的 aria-label。 */
  backToTopLabel: string;
}

export interface FlowPlayerCopy {
  title: string;
  hint: string;
  play: string;
  pause: string;
  prev: string;
  next: string;
  reset: string;
  frameLabel: (cur: number, total: number) => string;
  emptyMessages: string;
  messagesHeading: string;
  modelResponseLabel: string;
  toolCallLabel: string;
}

/* HarnessMovie 专用文案。和 FlowPlayer 分开维护，因为它的隐喻不一样：
   FlowPlayer 是「节点跳」；HarnessMovie 是「Action Card 穿过 harness 边界」。 */
export interface HarnessMovieCopy {
  sectionLabel: string;
  taskLabel: string;
  ledgerLabel: string;
  play: string;
  pause: string;
  prev: string;
  next: string;
  reset: string;
  frameLabel: (cur: number, total: number) => string;
  inspectorToggle: (open: boolean) => string;
  gateStates: { open: string; blocked: string; ask: string };
  cardKinds: {
    user_input: string;
    tool_use: string;
    tool_result: string;
    final: string;
  };
  emptyLedger: string;
}

export interface DiffCardCopy {
  badge: string;
  loading: string;
}

export interface TerminalReplayCopy {
  title: string;
  run: string;
  reset: string;
  loading: string;
}

interface SiteCopy {
  chrome: ChromeCopy;
  docs: DocsCopy;
  flow: FlowPlayerCopy;
  movie: HarnessMovieCopy;
  diff: DiffCardCopy;
  terminal: TerminalReplayCopy;
}

export const COPY: Record<Locale, SiteCopy> = {
  zh: {
    chrome: {
      brand: 'BuildCC',
      footerSmallprint: 'BuildCC · 教学项目 · 与官方 Claude Code CLI 无关',
      footerLinks: {
        repo: '源码',
        start: '从 Day 1 开始',
      },
    },
    docs: {
      sidebarHeading: '课程目录',
      sidebarSubheading: '14 天路线 · 顺序学习',
      tocLabel: '本章目录',
      langSwitcherLabel: '语言',
      dayOfTotal: (cur, total) => `第 ${cur} 天 / 共 ${total} 天`,
      prevLessonLabel: '上一篇',
      nextLessonLabel: '下一篇',
      comingSoonLabel: '即将上线',
      backToTopLabel: '回到顶部',
    },
    flow: {
      title: 'Agent Loop 播放器',
      hint: '点击节点跳到对应帧，或者让它自动播放。',
      play: '播放',
      pause: '暂停',
      prev: '上一步',
      next: '下一步',
      reset: '重置',
      frameLabel: (cur, total) => `帧 ${cur} / ${total}`,
      emptyMessages: '尚无对话上下文。',
      messagesHeading: 'Messages 上下文',
      modelResponseLabel: 'Model Response',
      toolCallLabel: 'Tool Call',
    },
    movie: {
      sectionLabel: '执行回放',
      taskLabel: '任务',
      ledgerLabel: 'State Ledger',
      play: '播放',
      pause: '暂停',
      prev: '上一步',
      next: '下一步',
      reset: '重置',
      frameLabel: (cur, total) => `${cur} / ${total}`,
      inspectorToggle: (open) => (open ? '收起 messages' : '展开 messages'),
      gateStates: { open: '放行', blocked: '拒绝', ask: '需确认' },
      cardKinds: {
        user_input: '用户输入',
        tool_use: '模型提议',
        tool_result: '工具结果',
        final: '最终回复',
      },
      emptyLedger: 'ledger 为空，等模型迈出第一步。',
    },
    diff: {
      badge: '代码改动',
      loading: '加载代码改动中…',
    },
    terminal: {
      title: 'terminal — zsh',
      run: '运行 Demo',
      reset: '重置',
      loading: '加载终端记录中…',
    },
  },

  en: {
    chrome: {
      brand: 'BuildCC',
      footerSmallprint: 'BuildCC · teaching project · not affiliated with the official Claude Code CLI',
      footerLinks: {
        repo: 'Source',
        start: 'Start with Day 1',
      },
    },
    docs: {
      sidebarHeading: 'Curriculum',
      sidebarSubheading: '14-day path · learn in order',
      tocLabel: 'On this page',
      langSwitcherLabel: 'Language',
      dayOfTotal: (cur, total) => `Day ${cur} of ${total}`,
      prevLessonLabel: 'Previous',
      nextLessonLabel: 'Next',
      comingSoonLabel: 'Coming soon',
      backToTopLabel: 'Back to top',
    },
    flow: {
      title: 'Agent Loop player',
      hint: 'Click a node to jump, or hit play to autoscrub.',
      play: 'Play',
      pause: 'Pause',
      prev: 'Prev',
      next: 'Next',
      reset: 'Reset',
      frameLabel: (cur, total) => `Frame ${cur} / ${total}`,
      emptyMessages: 'No conversation yet.',
      messagesHeading: 'Messages',
      modelResponseLabel: 'Model Response',
      toolCallLabel: 'Tool Call',
    },
    movie: {
      sectionLabel: 'Run trace',
      taskLabel: 'Task',
      ledgerLabel: 'State Ledger',
      play: 'Play',
      pause: 'Pause',
      prev: 'Prev',
      next: 'Next',
      reset: 'Reset',
      frameLabel: (cur, total) => `${cur} / ${total}`,
      inspectorToggle: (open) => (open ? 'Hide messages' : 'Show messages'),
      gateStates: { open: 'allow', blocked: 'block', ask: 'ask' },
      cardKinds: {
        user_input: 'user input',
        tool_use: 'model proposes',
        tool_result: 'tool result',
        final: 'final reply',
      },
      emptyLedger: 'ledger empty — waiting for the first move.',
    },
    diff: {
      badge: 'Code change',
      loading: 'Loading diff…',
    },
    terminal: {
      title: 'terminal — zsh',
      run: 'Run demo',
      reset: 'Reset',
      loading: 'Loading trace…',
    },
  },
};
