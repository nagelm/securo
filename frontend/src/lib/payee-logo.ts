/** Payee logos as the server builds and stores them.
 *
 *  Mirrors backend/app/providers/favicon.py (`favicon_url_for`) and
 *  backend/app/schemas/payee.py (`normalize_logo_url`), so the payee form can
 *  preview a logo before it is saved. Keep the three in step.
 */

/** Google's favicon size the server uses (`LOGO_SIZE`, served by
 *  GET /settings/logos); the value to assume until that answers. */
export const DEFAULT_LOGO_SIZE = 128

/** Longest logo URL the server stores. */
export const LOGO_URL_MAX_LENGTH = 500

// A host typed without a scheme, optionally with a port and a path. Needs a
// dot, so a "javascript:..." or "data:..." value can never pass for one.
const SCHEMELESS_URL = /^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d+)?(\/\S*)?$/

/** The bare domain of a website ("www." and scheme stripped), or null. */
export function extractDomain(website: string | null | undefined): string | null {
  const value = website?.trim()
  if (!value) return null
  let host: string
  try {
    host = new URL(value.includes('://') ? value : `https://${value}`).hostname.toLowerCase()
  } catch {
    return null
  }
  if (!host) return null
  return host.startsWith('www.') ? host.slice(4) : host
}

/** The favicon-based logo for a website, as the server builds it, or null. */
export function faviconUrlFor(
  website: string | null | undefined,
  size: number = DEFAULT_LOGO_SIZE,
): string | null {
  const domain = extractDomain(website)
  if (!domain) return null
  return `https://www.google.com/s2/favicons?domain=${domain}&sz=${size}`
}

export type LogoUrlInput =
  | { status: 'empty' }
  | { status: 'invalid' }
  | { status: 'ok'; url: string }

/** What a typed logo URL will be stored as.
 *
 *  Only http(s) is accepted. A website rather than an image (a bare domain,
 *  or a URL with nothing after the host) becomes that site's favicon; any
 *  URL with a path is taken as the image itself.
 */
export function normalizeLogoUrl(raw: string, size: number = DEFAULT_LOGO_SIZE): LogoUrlInput {
  let value = raw.trim()
  if (!value) return { status: 'empty' }
  if (value.length > LOGO_URL_MAX_LENGTH || /\s/.test(value)) return { status: 'invalid' }
  if (!value.includes('://')) {
    if (!SCHEMELESS_URL.test(value)) return { status: 'invalid' }
    value = `https://${value}`
  }
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return { status: 'invalid' }
  }
  if ((parsed.protocol !== 'http:' && parsed.protocol !== 'https:') || !parsed.hostname) {
    return { status: 'invalid' }
  }
  if (parsed.pathname === '/' && !parsed.search) {
    const favicon = faviconUrlFor(value, size)
    return favicon ? { status: 'ok', url: favicon } : { status: 'invalid' }
  }
  return { status: 'ok', url: value }
}
