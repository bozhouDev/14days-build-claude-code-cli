import { createI18nMiddleware } from 'fumadocs-core/i18n/middleware';
import { i18n } from './lib/i18n';

/* `hideLocale: 'default-locale'` 让默认中文走 `/`，英文走 `/en/...`。
   middleware 只负责把没有前缀的请求映射到默认语言的实际路由。 */
export default createI18nMiddleware(i18n);

export const config = {
  // 跳过 Next.js 内部 + 静态资源
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|lessons|images|.*\\.\\w+).*)'],
};
