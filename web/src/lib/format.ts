const STRATEGY_LABELS: Record<string, string> = {
  ua: 'publisher User-Agent',
  'ua:googlebot': 'crawler User-Agent',
  'ua:bingbot': 'crawler User-Agent',
  'ua:custom': 'custom User-Agent',
  referer: 'referer trick',
  'referer:google': 'Google referer',
  block_js: 'JS paywall',
  archive: 'archive snapshot',
  cookies: 'cookie gate',
  generic: 'generic path',
}

/** Turn a bypass_type into words a reader understands. */
export function strategyLabel(bypassType: string): string {
  if (STRATEGY_LABELS[bypassType]) return STRATEGY_LABELS[bypassType]
  const head = bypassType.split(':')[0]
  return STRATEGY_LABELS[head] ?? bypassType
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}
