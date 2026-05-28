import { docs, meta } from '../.source/server';
import { toFumadocsSource } from 'fumadocs-mdx/runtime/server';
import { loader } from 'fumadocs-core/source';
import { i18n } from './i18n';

/* Fumadocs 新版 API：collection entries 通过 `toFumadocsSource` 适配进 loader。
   `parser: 'dot'`（默认）按文件名 `slug.lang.mdx` 拆分多语言版本。 */
export const source = loader({
  baseUrl: '/docs',
  i18n,
  source: toFumadocsSource(docs, meta),
});
