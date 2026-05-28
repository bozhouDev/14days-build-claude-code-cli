import { defaultLocale, type Locale } from './i18n';

/* hideLocale: 'default-locale' 模式下：
   - 默认语言（zh）不带前缀：/docs/day-01-hello-agent
   - 其它语言（en）带前缀：/en/docs/day-01-hello-agent
   所有内部链接都过这个函数生成。 */
export function localizedHref(path: string, locale: Locale): string {
  if (!path.startsWith('/')) path = `/${path}`;
  if (locale === defaultLocale) return path;
  return `/${locale}${path}`;
}
