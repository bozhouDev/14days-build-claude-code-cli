'use client';

import { useState } from 'react';
import { usePathname } from 'next/navigation';
import { localeFromPathname } from '@/lib/i18n';

interface ConceptProps {
  name: string;
  children: React.ReactNode;
}

interface GlossaryEntry {
  definition: string;
  location: string;
}

const GLOSSARY: Record<string, Record<'zh' | 'en', GlossaryEntry>> = {
  Provider: {
    zh: {
      definition: '真正调用大语言模型的模块，负责把 messages 翻译成对方 API 的输入，再把响应解析回 ModelResponse。',
      location: 'agent_code/providers/*.py',
    },
    en: {
      definition: 'The piece that actually calls the LLM. It maps messages to the wire API and parses the response back into ModelResponse.',
      location: 'agent_code/providers/*.py',
    },
  },
  Harness: {
    zh: {
      definition: '模型外层的工程运行时：CLI、文件读取、命令执行、权限检查、上下文管理都属于 harness。',
      location: 'cli.py · agent.py',
    },
    en: {
      definition: 'Everything around the model: CLI, file I/O, command exec, permissions, context management.',
      location: 'cli.py · agent.py',
    },
  },
  Tool: {
    zh: {
      definition: '交给模型差遣的一个 Python 函数，配上名字和说明后注册进 ToolRegistry。模型只能请求它，真正执行的是 harness。',
      location: 'tools.py (Tool)',
    },
    en: {
      definition: 'A Python function you expose to the model. With a name and description it is registered in ToolRegistry. The model can only request it; the harness runs it.',
      location: 'tools.py (Tool)',
    },
  },
  'Tool Call': {
    zh: {
      definition: '模型发给 Harness 的执行意图，包含工具名（如 echo）和参数。',
      location: 'model.py (ToolCall)',
    },
    en: {
      definition: 'An execution intent emitted by the model, containing a tool name (e.g. echo) and arguments.',
      location: 'model.py (ToolCall)',
    },
  },
  'Function Call': {
    zh: {
      definition: '模型“下单”调用工具的动作；function call / tool call / tool use 是同一件事，只是各家叫法不同。模型只输出请求，不自己执行。',
      location: 'model.py (ToolCall)',
    },
    en: {
      definition: 'The model placing an order to call a tool; function call / tool call / tool use are the same thing under different vendor names. The model only emits the request — it never runs the function itself.',
      location: 'model.py (ToolCall)',
    },
  },
  Observation: {
    zh: {
      definition: '工具执行后的观察结果。Harness 把它喂回 messages，让模型决定下一步。',
      location: 'agent.py · tools.py',
    },
    en: {
      definition: 'The result observed after running a tool. Harness feeds it back into messages so the model can decide the next move.',
      location: 'agent.py · tools.py',
    },
  },
  'Tool Result': {
    zh: {
      definition: 'Harness 打包好的工具执行结果，最终作为消息 append 进 messages 数组。',
      location: 'model.py (ToolResult)',
    },
    en: {
      definition: 'Harness-wrapped tool output that gets appended to messages as a structured entry.',
      location: 'model.py (ToolResult)',
    },
  },
  Messages: {
    zh: {
      definition: '会话历史数组：user、assistant.tool_use、user.tool_result 依次累积，是 Agent Loop 的中央数据结构。',
      location: 'agent.py',
    },
    en: {
      definition: 'The running conversation history: user, assistant.tool_use, user.tool_result entries accumulated in order — the central data structure of the Agent Loop.',
      location: 'agent.py',
    },
  },
  Schema: {
    zh: {
      definition: '告诉模型工具参数形状的 JSON 对象（字段名、类型、是否必填）。',
      location: 'tools.py',
    },
    en: {
      definition: 'A JSON object that describes the tool argument shape (field names, types, required).',
      location: 'tools.py',
    },
  },
};

/* 正文里的术语 hover popover：浅紫底虚线下划线，悬停弹出 Notion 风浅卡。 */
export default function ConceptPopover({ name, children }: ConceptProps) {
  const [visible, setVisible] = useState(false);
  const pathname = usePathname();
  const locale = localeFromPathname(pathname);
  const entry = GLOSSARY[name]?.[locale] ?? {
    definition: locale === 'en' ? 'Undefined concept' : '未定义概念',
    location: '—',
  };

  return (
    <span
      className="relative inline-block"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
    >
      <span className="cursor-help border-b border-dashed border-[color:var(--ntn-primary)]/60 text-[color:var(--ntn-ink-deep)] hover:text-[color:var(--ntn-primary)] transition-colors">
        {children}
      </span>
      {visible && (
        <span className="ntn-anim-in absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-[300px] p-4 rounded-[var(--ntn-rounded-md)] border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)] ntn-shadow-4 text-left">
          <span className="inline-flex items-center px-1.5 py-0.5 rounded-[var(--ntn-rounded-xs)] bg-[color:var(--ntn-tint-lavender)] text-[color:var(--ntn-brand-purple-800)] text-[10.5px] font-semibold uppercase tracking-wider mb-2">
            {name}
          </span>
          <span className="block text-[13px] leading-relaxed text-[color:var(--ntn-charcoal)]">
            {entry.definition}
          </span>
          <span className="block mt-2 pt-2 border-t border-[color:var(--ntn-hairline-soft)] text-[11px] font-[var(--ntn-font-mono)] text-[color:var(--ntn-stone)]">
            {entry.location}
          </span>
        </span>
      )}
    </span>
  );
}
