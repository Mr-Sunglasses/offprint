import { useEffect, useState } from 'react'
import type { DiscoverResult } from '../types'
import { discover } from '../api'
import { Icon } from './Icon'

interface Props {
  onFetchUrl: (url: string) => void
  onFetchMany: (urls: string[]) => void
  initialSite?: string
}

const QUICK_SITES = ['nytimes.com', 'economist.com', 'ft.com', 'wired.com', 'spiegel.de', 'elpais.com']

const SINCE_OPTIONS = [
  { value: 'today', label: 'Today' },
  { value: '3d', label: '3 days' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
]

export function DiscoverPanel({ onFetchUrl, onFetchMany, initialSite }: Props) {
  const [site, setSite] = useState(initialSite ?? '')
  const [since, setSince] = useState('7d')
  const [result, setResult] = useState<DiscoverResult | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (initialSite) setSite(initialSite)
  }, [initialSite])

  async function run(target = site) {
    const cleaned = target.trim()
    if (!cleaned || loading) return
    setLoading(true)
    setError('')
    setResult(null)
    setSelected(new Set())
    try {
      setResult(await discover(cleaned, since, 20))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  function toggle(url: string) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(url)) next.delete(url)
      else next.add(url)
      return next
    })
  }

  return (
    <section className="discover">
      <form
        className="discover-bar"
        onSubmit={(event) => {
          event.preventDefault()
          void run()
        }}
      >
        <Icon name="search" size={16} />
        <input
          className="discover-input"
          value={site}
          onChange={(event) => setSite(event.target.value)}
          placeholder="Which site? nytimes.com, ft.com, economist.com…"
          spellCheck={false}
        />
        <select
          className="discover-select"
          value={since}
          onChange={(event) => setSince(event.target.value)}
          aria-label="Time range"
        >
          {SINCE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <button className="primary-btn" type="submit" disabled={loading || !site.trim()}>
          {loading ? <Icon name="spinner" size={15} className="spin" /> : 'Find articles'}
        </button>
      </form>

      <div className="quick-row discover-quick">
        {QUICK_SITES.map((domain) => (
          <button key={domain} className="quick-chip" onClick={() => void run(domain)}>
            {domain}
          </button>
        ))}
      </div>

      {error && <p className="notice notice-warn">{error}</p>}

      {result && (
        <div className="discover-results">
          <div className="discover-head">
            <span>
              <strong>{result.site}</strong> · {result.count} recent · {result.source}
            </span>
            <div className="discover-head-actions">
              <button
                className="tool-btn"
                disabled={selected.size === 0}
                onClick={() => onFetchMany([...selected])}
              >
                <Icon name="download" size={14} />
                Fetch {selected.size || ''} selected
              </button>
              <button
                className="tool-btn"
                onClick={() => onFetchMany(result.articles.slice(0, 10).map((a) => a.url))}
              >
                Fetch top 10
              </button>
            </div>
          </div>

          <ul className="discover-list">
            {result.articles.map((article) => (
              <li key={article.url} className="discover-item">
                <input
                  type="checkbox"
                  className="discover-check"
                  checked={selected.has(article.url)}
                  onChange={() => toggle(article.url)}
                  aria-label={`Select ${article.title}`}
                />
                <div className="discover-item-main">
                  <button className="discover-title" onClick={() => onFetchUrl(article.url)}>
                    {article.title}
                  </button>
                  <span className="discover-meta" title={article.summary || undefined}>
                    {article.published || 'undated'}
                    {article.summary ? ` · ${article.summary}` : ''}
                  </span>
                </div>
                <button className="batch-item-action" onClick={() => onFetchUrl(article.url)} title="Fetch this article">
                  <Icon name="arrow" size={13} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!result && !error && !loading && (
        <p className="discover-hint">
          Discovery reads the site&apos;s own RSS feed (or sitemap) — no search API keys, no crawling
          the whole site. Pick a title to fetch it, or select several and batch them.
        </p>
      )}
    </section>
  )
}
