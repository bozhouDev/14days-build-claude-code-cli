'use client';

import { User, Cpu, Wrench, MessageSquare } from 'lucide-react';
import { COPY } from '@/lib/copy';

interface Message {
  role: string;
  content: string;
  tool_call_id?: string;
}

interface ModelResponse {
  text?: string;
  tool_calls?: Array<{
    id: string;
    name: string;
    arguments: Record<string, any>;
  }>;
  stop_reason?: string;
}

interface MessageInspectorProps {
  messages: Message[];
  modelResponse?: ModelResponse | null;
  locale?: 'zh' | 'en';
}

/* role → tint：呼应 Notion database property color chips。
   user=lavender / assistant=sky / tool=peach / tool_result=mint */
const ROLE_STYLE: Record<
  string,
  { bg: string; chipBg: string; chipFg: string; iconColor: string }
> = {
  user: {
    bg: 'bg-[color:var(--ntn-tint-lavender)]',
    chipBg: 'bg-[color:var(--ntn-canvas)]',
    chipFg: 'text-[color:var(--ntn-brand-purple-800)]',
    iconColor: 'text-[color:var(--ntn-primary-deep)]',
  },
  assistant: {
    bg: 'bg-[color:var(--ntn-tint-sky)]',
    chipBg: 'bg-[color:var(--ntn-canvas)]',
    chipFg: 'text-[color:var(--ntn-link-blue-pressed)]',
    iconColor: 'text-[color:var(--ntn-link-blue-pressed)]',
  },
  tool: {
    bg: 'bg-[color:var(--ntn-tint-peach)]',
    chipBg: 'bg-[color:var(--ntn-canvas)]',
    chipFg: 'text-[color:var(--ntn-brand-orange-deep)]',
    iconColor: 'text-[color:var(--ntn-brand-orange-deep)]',
  },
  tool_result: {
    bg: 'bg-[color:var(--ntn-tint-mint)]',
    chipBg: 'bg-[color:var(--ntn-canvas)]',
    chipFg: 'text-[color:var(--ntn-success)]',
    iconColor: 'text-[color:var(--ntn-success)]',
  },
};

function roleStyle(role: string) {
  return ROLE_STYLE[role] ?? ROLE_STYLE.assistant;
}

export default function MessageInspector({
  messages,
  modelResponse,
  locale,
}: MessageInspectorProps) {
  const lang =
    locale ??
    (typeof document !== 'undefined' && document.documentElement.lang === 'en'
      ? 'en'
      : 'zh');
  const copy = COPY[lang].flow;

  return (
    <div className="ntn-card-soft p-4">
      <div className="flex items-center justify-between pb-3 border-b border-[color:var(--ntn-hairline)] mb-3">
        <div className="flex items-center gap-2">
          <MessageSquare size={13} className="text-[color:var(--ntn-primary)]" />
          <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[color:var(--ntn-stone)]">
            {copy.messagesHeading}
          </span>
        </div>
        <span className="text-[10.5px] font-[var(--ntn-font-mono)] text-[color:var(--ntn-stone)]">
          messages.length = {messages.length}
        </span>
      </div>

      {messages.length === 0 && !modelResponse && (
        <div className="py-6 text-center text-[13px] text-[color:var(--ntn-slate)]">
          {copy.emptyMessages}
        </div>
      )}

      <div className="space-y-2 max-h-[320px] overflow-y-auto pr-1">
        {messages.map((msg, idx) => {
          const isUser = msg.role === 'user';
          const isTool = msg.role === 'tool';
          const Icon = isUser ? User : isTool ? Wrench : Cpu;
          const style = roleStyle(msg.role);

          return (
            <div
              key={idx}
              className={`ntn-anim-in flex items-start gap-2.5 p-3 rounded-[var(--ntn-rounded-md)] ${style.bg}`}
            >
              <span className={`mt-0.5 ${style.iconColor}`}>
                <Icon size={13} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between mb-1">
                  <span
                    className={`inline-flex items-center px-1.5 py-0.5 rounded-[var(--ntn-rounded-xs)] text-[10.5px] font-semibold uppercase tracking-wider ${style.chipBg} ${style.chipFg}`}
                  >
                    {msg.role}
                  </span>
                  {msg.tool_call_id && (
                    <span className="text-[10px] font-[var(--ntn-font-mono)] text-[color:var(--ntn-stone)]">
                      {msg.tool_call_id}
                    </span>
                  )}
                </div>
                <p className="text-[13px] leading-relaxed text-[color:var(--ntn-ink-deep)] whitespace-pre-wrap break-words font-[var(--ntn-font-mono)]">
                  {msg.content}
                </p>
              </div>
            </div>
          );
        })}

        {modelResponse && (
          <div className="ntn-anim-in p-3 rounded-[var(--ntn-rounded-md)] bg-[color:var(--ntn-tint-lavender)] border-l-2 border-[color:var(--ntn-primary)]">
            <div className="flex items-center justify-between mb-1.5">
              <span className="inline-flex items-center px-1.5 py-0.5 rounded-[var(--ntn-rounded-xs)] bg-[color:var(--ntn-canvas)] text-[color:var(--ntn-primary-deep)] text-[10.5px] font-semibold uppercase tracking-wider">
                {copy.modelResponseLabel}
              </span>
              {modelResponse.stop_reason && (
                <span className="text-[10px] font-[var(--ntn-font-mono)] text-[color:var(--ntn-stone)]">
                  stop_reason: {modelResponse.stop_reason}
                </span>
              )}
            </div>
            {modelResponse.text && (
              <p className="text-[13px] leading-relaxed text-[color:var(--ntn-ink-deep)] font-[var(--ntn-font-mono)]">
                {modelResponse.text}
              </p>
            )}
            {modelResponse.tool_calls && (
              <div className="mt-2 pt-2 border-t border-[color:var(--ntn-hairline-soft)] space-y-1.5">
                <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[color:var(--ntn-primary-deep)]">
                  {copy.toolCallLabel}
                </span>
                {modelResponse.tool_calls.map((tc) => (
                  <div
                    key={tc.id}
                    className="rounded-[var(--ntn-rounded-sm)] bg-[color:var(--ntn-canvas)] border border-[color:var(--ntn-hairline)] p-2 text-[11.5px] font-[var(--ntn-font-mono)] space-y-0.5"
                  >
                    <div>
                      <span className="text-[color:var(--ntn-stone)]">name: </span>
                      <span className="text-[color:var(--ntn-primary-deep)] font-semibold">{tc.name}</span>
                    </div>
                    <div>
                      <span className="text-[color:var(--ntn-stone)]">args: </span>
                      <span className="text-[color:var(--ntn-ink-deep)]">
                        {JSON.stringify(tc.arguments)}
                      </span>
                    </div>
                    <div>
                      <span className="text-[color:var(--ntn-stone)]">id: </span>
                      <span className="text-[color:var(--ntn-slate)]">{tc.id}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
