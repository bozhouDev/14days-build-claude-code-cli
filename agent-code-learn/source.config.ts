import { defineConfig, defineDocs } from 'fumadocs-mdx/config';

export const { docs, meta } = defineDocs({
  dir: 'content/docs',
});

/* Shiki 双主题输出 --shiki-light / --shiki-dark CSS 变量，Fumadocs 按 .dark class 切换。
   Notion 风的代码面板在亮 / 暗页面模式下都是深底（--ntn-code-bg），
   所以两边都用 dark 系 token —— 但不能写成同一个名字，
   否则 Shiki 会去重只输出 --shiki-light，dark 模式拿不到颜色就回退成纯白没高亮。
   用 github-dark + github-dark-dimmed 两个同色系深主题，保留差异 / 防去重，
   且暗模式下 dimmed 略柔和，跟整体深背景更贴。 */
export default defineConfig({
  mdxOptions: {
    rehypeCodeOptions: {
      themes: {
        light: 'github-dark',
        dark: 'github-dark-dimmed',
      },
    },
  },
});
