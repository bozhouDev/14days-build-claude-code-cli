import { notFound } from 'next/navigation';
import defaultComponents from 'fumadocs-ui/mdx';
import { source } from '@/lib/source';
import LessonSidebar from '@/components/site/LessonSidebar';
import TocPanel from '@/components/site/TocPanel';
import LessonHero from '@/components/site/LessonHero';
import LessonPager from '@/components/site/LessonPager';
import ReadingProgress from '@/components/site/ReadingProgress';
import BackToTop from '@/components/site/BackToTop';
import DiffCard from '@/components/lesson/DiffCard';
import AgentLogicMap from '@/components/lesson/AgentLogicMap';
import AgentFlowPlayer from '@/components/lesson/AgentFlowPlayer';
import HarnessMovie from '@/components/lesson/HarnessMovie';
import TerminalReplay from '@/components/lesson/TerminalReplay';
import MessageInspector from '@/components/lesson/MessageInspector';
import ConceptPopover from '@/components/lesson/ConceptPopover';
import { COPY } from '@/lib/copy';
import { defaultLocale, locales, type Locale } from '@/lib/i18n';
import { getLessonNeighbors } from '@/lib/lessons';

interface Params {
  lang: string;
  slug?: string[];
}

/* 极简 inline markdown：只解析 `code` 反引号。
   frontmatter 的 description 不走 MDX pipeline，但作者会自然在里面写 `agent-code`
   这种行内代码，所以这里手动把反引号片段渲成 <code>。
   样式刻意复刻 .ntn-prose code，因为 description 段落在 .ntn-prose 之外，没法继承。 */
function renderInlineDescription(text: string): React.ReactNode {
  const parts = text.split(/(`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length >= 2) {
      return (
        <code
          key={i}
          className="font-[var(--ntn-font-mono)] text-[0.92em] bg-[color:var(--ntn-surface)] border border-[color:var(--ntn-hairline)] rounded-[var(--ntn-rounded-xs)] px-[0.4em] py-[0.05em] text-[color:var(--ntn-charcoal)]"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

export default async function LessonPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { lang, slug } = await params;
  const locale = (locales as string[]).includes(lang) ? (lang as Locale) : defaultLocale;
  const page = source.getPage(slug, locale);
  if (!page) notFound();

  /* fumadocs-mdx 真正注入的是 DocData & frontmatter，类型上是 PageData；
     这里窄断言取 body 和 toc。toc 是 fumadocs 从 h2/h3/h4 自动抽出的目录数组。 */
  const data = page.data as unknown as {
    body: React.FC<{ components?: Record<string, unknown> }>;
    toc?: Array<{ title: React.ReactNode; url: string; depth: number }>;
  };
  const MDX = data.body;
  const toc = data.toc ?? [];
  const currentSlug = slug?.[0];
  const docsCopy = COPY[locale].docs;

  /* 一次性查出当前 lesson 的位置 + 上下篇，喂给 hero 和 pager。
     slug 不在 lessons.ts 表里也安全（neighbors.index = 0，hero 自然降级）。 */
  const neighbors = currentSlug
    ? getLessonNeighbors(currentSlug, locale)
    : { index: 0, total: 14 };

  return (
    <>
      <ReadingProgress />

      <div className="max-w-[1400px] mx-auto px-6 flex gap-8">
        <LessonSidebar locale={locale} currentSlug={currentSlug} />

        <article className="min-w-0 flex-1 max-w-[820px] py-10">
          <LessonHero
            locale={locale}
            current={neighbors.current}
            index={neighbors.index}
            total={neighbors.total}
            title={page.data.title}
            description={
              page.data.description
                ? renderInlineDescription(page.data.description)
                : undefined
            }
          />

          <div className="ntn-prose">
            <MDX
              components={{
                ...defaultComponents,
                DiffCard,
                AgentLogicMap,
                AgentFlowPlayer,
                HarnessMovie,
                TerminalReplay,
                MessageInspector,
                ConceptPopover,
                Concept: ConceptPopover,
              }}
            />
          </div>

          <LessonPager locale={locale} prev={neighbors.prev} next={neighbors.next} />
        </article>

        <TocPanel toc={toc} label={docsCopy.tocLabel} />
      </div>

      <BackToTop label={docsCopy.backToTopLabel} />
    </>
  );
}

export async function generateStaticParams() {
  const params = source.generateParams();
  return params;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}) {
  const { lang, slug } = await params;
  const locale = (locales as string[]).includes(lang) ? (lang as Locale) : defaultLocale;
  const page = source.getPage(slug, locale);
  if (!page) return {};
  return {
    title: page.data.title,
    description: page.data.description,
  };
}
