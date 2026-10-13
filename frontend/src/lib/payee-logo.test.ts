import { describe, expect, it } from 'vitest'

import { extractDomain, faviconUrlFor, normalizeLogoUrl } from '@/lib/payee-logo'

describe('faviconUrlFor', () => {
  it('builds the same favicon URL as the server', () => {
    expect(faviconUrlFor('https://www.Shop.example/about')).toBe(
      'https://www.google.com/s2/favicons?domain=shop.example&sz=128',
    )
    expect(faviconUrlFor('shop.example', 64)).toBe(
      'https://www.google.com/s2/favicons?domain=shop.example&sz=64',
    )
  })

  it('gives nothing for a blank website', () => {
    expect(faviconUrlFor('  ')).toBeNull()
    expect(extractDomain(null)).toBeNull()
  })
})

describe('normalizeLogoUrl', () => {
  it('keeps an image URL as typed, trimmed', () => {
    expect(normalizeLogoUrl('  https://img.example/logo.png ')).toEqual({
      status: 'ok', url: 'https://img.example/logo.png',
    })
    expect(normalizeLogoUrl('cdn.shop.example/brand/logo')).toEqual({
      status: 'ok', url: 'https://cdn.shop.example/brand/logo',
    })
  })

  it('turns a website into its favicon', () => {
    const favicon = 'https://www.google.com/s2/favicons?domain=shop.example&sz=128'
    expect(normalizeLogoUrl('www.shop.example')).toEqual({ status: 'ok', url: favicon })
    expect(normalizeLogoUrl('https://shop.example/')).toEqual({ status: 'ok', url: favicon })
  })

  it('treats a blank value as clearing the logo', () => {
    expect(normalizeLogoUrl('   ')).toEqual({ status: 'empty' })
  })

  it.each([
    'javascript:alert(1)',
    'data:image/png;base64,AAAA',
    'ftp://shop.example/logo.png',
    'not a url',
    `https://${'a'.repeat(495)}.png`,
  ])('rejects %s', (value) => {
    expect(normalizeLogoUrl(value)).toEqual({ status: 'invalid' })
  })
})
