'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePathname } from 'next/navigation';
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import {
  Boxes,
  Braces,
  Cpu,
  Database,
  FileOutput,
  GitBranch,
  Terminal,
  type LucideIcon,
} from 'lucide-react';
import { localeFromPathname } from '@/lib/i18n';

type LogicNodeKind = 'runtime' | 'data' | 'model' | 'response' | 'tool' | 'output';

interface LogicMapNode {
  id: string;
  kind: LogicNodeKind;
  label: string;
  eyebrow?: string;
  body: string;
  detail: string;
  position: { x: number; y: number };
  mobilePosition?: { x: number; y: number };
}

interface LogicMapEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  mobileLabel?: string;
  sourceHandle?: string;
  targetHandle?: string;
  mobileSourceHandle?: string;
  mobileTargetHandle?: string;
}

interface LogicDiagram {
  id: string;
  label: string;
  title: string;
  summary: string;
  defaultActiveNodeId: string;
  nodes: LogicMapNode[];
  edges: LogicMapEdge[];
}

interface LogicMapData {
  eyebrow: string;
  title: string;
  subtitle: string;
  diagrams: LogicDiagram[];
}

interface AgentLogicMapProps {
  mapPath: string;
}

interface LogicNodeData extends Record<string, unknown> {
  kind: LogicNodeKind;
  label: string;
  eyebrow?: string;
  body: string;
  isActive: boolean;
}

type LogicFlowNode = Node<LogicNodeData, 'logicNode'>;
type LogicFlowEdge = Edge<Record<string, never>, 'smoothstep'>;

const HANDLE_CLASS = '!h-2 !w-2 !border-0 !bg-transparent';

const KIND_STYLE: Record<
  LogicNodeKind,
  { icon: LucideIcon; border: string; tint: string; iconBg: string; iconText: string }
> = {
  runtime: {
    icon: Terminal,
    border: 'border-[color:var(--ntn-primary)]',
    tint: 'bg-[color:var(--ntn-tint-lavender)]',
    iconBg: 'bg-[color:var(--ntn-primary)]',
    iconText: 'text-[color:var(--ntn-on-primary)]',
  },
  data: {
    icon: Database,
    border: 'border-[color:var(--ntn-link-blue-pressed)]',
    tint: 'bg-[color:var(--ntn-tint-sky)]',
    iconBg: 'bg-[color:var(--ntn-link-blue)]',
    iconText: 'text-white',
  },
  model: {
    icon: Cpu,
    border: 'border-[color:var(--ntn-brand-teal)]',
    tint: 'bg-[color:var(--ntn-tint-mint)]',
    iconBg: 'bg-[color:var(--ntn-brand-teal)]',
    iconText: 'text-white',
  },
  response: {
    icon: Braces,
    border: 'border-[color:var(--ntn-brand-orange-deep)]',
    tint: 'bg-[color:var(--ntn-tint-yellow)]',
    iconBg: 'bg-[color:var(--ntn-brand-orange)]',
    iconText: 'text-white',
  },
  tool: {
    icon: Boxes,
    border: 'border-[color:var(--ntn-brand-brown)]',
    tint: 'bg-[color:var(--ntn-tint-cream)]',
    iconBg: 'bg-[color:var(--ntn-brand-brown)]',
    iconText: 'text-white',
  },
  output: {
    icon: FileOutput,
    border: 'border-[color:var(--ntn-success)]',
    tint: 'bg-[color:var(--ntn-canvas)]',
    iconBg: 'bg-[color:var(--ntn-success)]',
    iconText: 'text-white',
  },
};

const nodeTypes = {
  logicNode: LogicNode,
};

function resolveMapPath(path: string, lang: 'zh' | 'en'): string {
  if (path.endsWith('.json')) return path.replace(/\.json$/, `.${lang}.json`);
  return path;
}

function LogicNode({ data }: NodeProps<LogicFlowNode>) {
  const style = KIND_STYLE[data.kind];
  const Icon = style.icon;

  return (
    <div
      className={[
        'w-[188px] rounded-[var(--ntn-rounded-md)] border bg-[color:var(--ntn-canvas)]',
        'px-3 py-3 ntn-shadow-1 transition-colors',
        data.isActive ? `${style.border} ${style.tint}` : 'border-[color:var(--ntn-hairline)]',
      ].join(' ')}
    >
      <Handle type="target" id="target-left" position={Position.Left} className={HANDLE_CLASS} />
      <Handle type="target" id="target-top" position={Position.Top} className={HANDLE_CLASS} />
      <Handle type="target" id="target-right" position={Position.Right} className={HANDLE_CLASS} />
      <Handle type="target" id="target-bottom" position={Position.Bottom} className={HANDLE_CLASS} />
      <Handle type="source" id="source-left" position={Position.Left} className={HANDLE_CLASS} />
      <Handle type="source" id="source-top" position={Position.Top} className={HANDLE_CLASS} />
      <Handle type="source" id="source-right" position={Position.Right} className={HANDLE_CLASS} />
      <Handle type="source" id="source-bottom" position={Position.Bottom} className={HANDLE_CLASS} />

      <div className="flex items-start gap-2">
        <span
          className={[
            'mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--ntn-rounded-sm)]',
            data.isActive ? `${style.iconBg} ${style.iconText}` : 'bg-[color:var(--ntn-surface)] text-[color:var(--ntn-slate)]',
          ].join(' ')}
        >
          <Icon size={14} />
        </span>
        <div className="min-w-0">
          {data.eyebrow && (
            <p className="mb-0.5 text-[10px] font-semibold uppercase text-[color:var(--ntn-stone)]">
              {data.eyebrow}
            </p>
          )}
          <p className="text-[13px] font-semibold leading-tight text-[color:var(--ntn-ink-deep)]">
            {data.label}
          </p>
        </div>
      </div>
      <p className="mt-2 text-[11.5px] leading-snug text-[color:var(--ntn-charcoal)]">
        {data.body}
      </p>
    </div>
  );
}

function edgeEndpoints(edge: LogicMapEdge, index: number) {
  const source = edge.source ?? (edge as { from?: string }).from ?? '';
  const target = edge.target ?? (edge as { to?: string }).to ?? '';
  const id = edge.id || `${source}-${target}-${index}`;
  return { id, source, target };
}

function activeEdges(edges: LogicMapEdge[], activeNodeId: string): Set<string> {
  const out = new Set<string>();
  edges.forEach((edge, index) => {
    const { id, source, target } = edgeEndpoints(edge, index);
    if (source === activeNodeId || target === activeNodeId) {
      out.add(id);
    }
  });
  return out;
}

export default function AgentLogicMap({ mapPath }: AgentLogicMapProps) {
  const pathname = usePathname();
  const lang = useMemo(() => localeFromPathname(pathname), [pathname]);

  const [logicMap, setLogicMap] = useState<LogicMapData | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeDiagramId, setActiveDiagramId] = useState<string | null>(null);
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);

  useEffect(() => {
    const path = resolveMapPath(mapPath, lang);
    setLoading(true);
    fetch(path)
      .then((r) => (r.ok ? r.json() : fetch(mapPath).then((rr) => rr.json())))
      .then((data: LogicMapData) => {
        setLogicMap(data);
        const firstDiagram = data.diagrams[0];
        setActiveDiagramId(firstDiagram?.id ?? null);
        setActiveNodeId(firstDiagram?.defaultActiveNodeId ?? firstDiagram?.nodes[0]?.id ?? null);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [mapPath, lang]);

  const [isCompact, setIsCompact] = useState(false);

  useEffect(() => {
    const query = window.matchMedia('(max-width: 640px)');
    const update = () => setIsCompact(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);

  const diagram = useMemo(() => {
    if (!logicMap) return null;
    return logicMap.diagrams.find((item) => item.id === activeDiagramId) ?? logicMap.diagrams[0] ?? null;
  }, [logicMap, activeDiagramId]);

  useEffect(() => {
    if (!diagram) return;
    setActiveNodeId(diagram.defaultActiveNodeId ?? diagram.nodes[0]?.id ?? null);
  }, [diagram?.id]);

  const hotEdges = useMemo(() => activeEdges(diagram?.edges ?? [], activeNodeId ?? ''), [diagram, activeNodeId]);

  const nodes = useMemo<LogicFlowNode[]>(() => {
    if (!diagram) return [];
    return diagram.nodes.map((node) => ({
      id: node.id,
      type: 'logicNode',
      position: isCompact && node.mobilePosition ? node.mobilePosition : node.position,
      data: {
        kind: node.kind,
        label: node.label,
        eyebrow: node.eyebrow,
        body: node.body,
        isActive: node.id === activeNodeId,
      },
      draggable: false,
      selectable: true,
    }));
  }, [diagram, activeNodeId, isCompact]);

  const edges = useMemo<LogicFlowEdge[]>(() => {
    if (!diagram) return [];
    return diagram.edges.map((edge, index) => {
      const source = edge.source ?? (edge as { from?: string }).from ?? '';
      const target = edge.target ?? (edge as { to?: string }).to ?? '';
      const edgeId = edge.id || `${source}-${target}-${index}`;
      const isHot = hotEdges.has(edgeId);
      const color = isHot ? 'var(--ntn-primary)' : 'var(--ntn-hairline-strong)';
      return {
        id: edgeId,
        source,
        target,
        sourceHandle: isCompact
          ? edge.mobileSourceHandle ?? edge.sourceHandle ?? 'source-right'
          : edge.sourceHandle ?? 'source-right',
        targetHandle: isCompact
          ? edge.mobileTargetHandle ?? edge.targetHandle ?? 'target-left'
          : edge.targetHandle ?? 'target-left',
        type: 'smoothstep',
        animated: isHot,
        label: isCompact ? edge.mobileLabel ?? edge.label : edge.label,
        style: {
          stroke: color,
          strokeWidth: isHot ? 2.3 : 1.4,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color,
          width: 16,
          height: 16,
        },
        labelStyle: {
          fill: 'var(--ntn-slate)',
          fontSize: 11,
          fontWeight: 600,
        },
        labelBgStyle: {
          fill: 'var(--ntn-canvas)',
          fillOpacity: 0.92,
        },
        labelBgPadding: [6, 3],
        labelBgBorderRadius: 4,
      };
    });
  }, [diagram, hotEdges, isCompact]);

  const activeNode = useMemo(() => {
    if (!diagram || !activeNodeId) return null;
    return diagram.nodes.find((node) => node.id === activeNodeId) ?? null;
  }, [diagram, activeNodeId]);

  if (loading || !logicMap || !diagram) {
    return (
      <div className="my-6 ntn-card-soft flex h-72 items-center justify-center text-[13px] text-[color:var(--ntn-slate)]">
        {lang === 'zh' ? '加载 Agent Logic Map 中…' : 'Loading Agent Logic Map…'}
      </div>
    );
  }

  return (
    <section className="my-8 ntn-card overflow-hidden">
      <div className="border-b border-[color:var(--ntn-hairline)] px-4 py-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase text-[color:var(--ntn-primary-deep)]">
              {logicMap.eyebrow}
            </p>
            <h4 className="mt-1 text-[16px] font-semibold leading-snug text-[color:var(--ntn-ink-deep)]">
              {logicMap.title}
            </h4>
            <p className="mt-1 max-w-[58ch] text-[12.5px] leading-relaxed text-[color:var(--ntn-slate)]">
              {logicMap.subtitle}
            </p>
          </div>

          <div className="flex shrink-0 gap-1 rounded-[var(--ntn-rounded-md)] border border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-surface)] p-1">
            {logicMap.diagrams.map((item) => {
              const isActive = item.id === diagram.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setActiveDiagramId(item.id)}
                  className={[
                    'rounded-[var(--ntn-rounded-sm)] px-2.5 py-1.5 text-[12px] font-semibold transition-colors',
                    isActive
                      ? 'bg-[color:var(--ntn-canvas)] text-[color:var(--ntn-ink-deep)] ntn-shadow-1'
                      : 'text-[color:var(--ntn-slate)] hover:text-[color:var(--ntn-ink-deep)]',
                  ].join(' ')}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="bg-[color:var(--ntn-surface-soft)]">
        <div className="border-b border-[color:var(--ntn-hairline)] px-4 py-3">
          <div className="flex items-start gap-2">
            <GitBranch size={14} className="mt-1 shrink-0 text-[color:var(--ntn-primary)]" />
            <div>
              <p className="text-[13px] font-semibold text-[color:var(--ntn-ink-deep)]">
                {diagram.title}
              </p>
              <p className="mt-0.5 text-[12.5px] leading-relaxed text-[color:var(--ntn-slate)]">
                {diagram.summary}
              </p>
            </div>
          </div>
        </div>

        <div className="h-[560px] md:h-[500px]">
          <ReactFlow
            key={diagram.id}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodeClick={(_, node) => setActiveNodeId(node.id)}
            nodesDraggable={false}
            nodesConnectable={false}
            edgesFocusable={false}
            zoomOnScroll={false}
            zoomOnPinch
            panOnScroll={false}
            preventScrolling={false}
            fitView
            fitViewOptions={{ padding: isCompact ? 0.12 : 0.14 }}
            minZoom={isCompact ? 0.48 : 0.46}
            maxZoom={1.2}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="rgba(118, 118, 123, 0.18)" gap={18} />
            <Controls showInteractive={false} position="bottom-right" />
          </ReactFlow>
        </div>
      </div>

      <div className="border-t border-[color:var(--ntn-hairline)] bg-[color:var(--ntn-canvas)] px-4 py-4">
        {activeNode && (
          <div className="border-l-2 border-[color:var(--ntn-primary)] pl-3">
            <p className="text-[11px] font-semibold uppercase text-[color:var(--ntn-stone)]">
              {lang === 'zh' ? '当前高亮' : 'Current focus'}
            </p>
            <p className="mt-1 text-[14px] font-semibold text-[color:var(--ntn-ink-deep)]">
              {activeNode.label}
            </p>
            <p className="mt-1 max-w-[68ch] text-[13px] leading-relaxed text-[color:var(--ntn-charcoal)]">
              {activeNode.detail}
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
