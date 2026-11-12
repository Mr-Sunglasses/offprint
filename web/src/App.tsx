import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Article, FetchOptions, Health, Job, Lookup } from './types'
import { getArticle, health as fetchHealth, lookup as fetchLookup, startFetch, waitForJob } from './api'
import { ArticleView } from './components/ArticleView'
import { BatchPanel } from './components/BatchPanel'
import { DiscoverPanel } from './components/DiscoverPanel'
import { FetchProgress } from './components/FetchProgress'
import { Header } from './components/Header'
import { HistoryStrip } from './components/HistoryStrip'
import { LibraryModal } from './components/LibraryModal'
import { SitesModal } from './components/SitesModal'
import { StatsModal } from './components/StatsModal'
import { UrlForm } from './components/UrlForm'
import { Icon } from './components/Icon'

type Phase = 'idle' | 'fetching' | 'done' | 'error'
type Mode = 'link' | 'batch' | 'latest'
type HistoryEntry = { url: string; title: string; site: string; at: number }

const HISTORY_KEY = 'offprint-history'
const THEME_KEY = 'offprint-theme'
const MAX_HISTORY = 8

const DEFAULT_OPTIONS: FetchOptions = {
  force_browser: false,
  skip_browser: false,
  use_archive: true,
  include_images: true,
  proxy_fallback: true,
}

const MODES: Array<{ key: Mode; label: string }> = [
  { key: 'link', label: 'One link' },
  { key: 'batch', label: 'Batch' },
  { key: 'latest', label: 'Latest' },
]

function readTheme(): 'light' | 'dark' {
  const stored = localStorage.getItem(THEME_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function readHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    return raw ? (JSON.parse(raw) as HistoryEntry[]) : []
  } catch {
    return []
  }
}

export default function App() {
  const [theme, setTheme] = useState<'light' | 'dark'>(readTheme)
  const [mode, setMode] = useState<Mode>('link')
  const [url, setUrl] = useState('')
  const [options, setOptions] = useState<FetchOptions>(DEFAULT_OPTIONS)
  const [phase, setPhase] = useState<Phase>('idle')
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [lookup, setLookup] = useState<Lookup | null>(null)
  const [info, setInfo] = useState<Health | null>(null)
  const [sitesOpen, setSitesOpen] = useState(false)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [statsOpen, setStatsOpen] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[]>(readHistory)
  const [elapsed, setElapsed] = useState(0)
  const [discoverSite, setDiscoverSite] = useState('')
  const [pendingBatch, setPendingBatch] = useState<string[]>([])

  const startedAt = useRef(0)
  const lookupTimer = useRef<number | undefined>(undefined)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  useEffect(() => {
    fetchHealth()
      .then(setInfo)
      .catch(() => setInfo(null))
  }, [])

  const openStored = useCallback((articleId: string) => {
    getArticle(articleId)
      .then((stored) => {
        setJob({
          id: '',
          url: stored.article.url,
          status: 'done',
          steps: [],
          result: stored,
          error: null,
          created_at: stored.fetched_at,
          finished_at: stored.fetched_at,
        })
        setUrl(stored.article.url)
        setPhase('done')
        setMode('link')
        const next = new URL(window.location.href)
        next.searchParams.set('a', articleId)
        window.history.replaceState({}, '', next)
        window.setTimeout(() => {
          document.querySelector('.reader')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }, 60)
      })
      .catch(() => {
        setPhase('error')
        setError('That article is no longer stored on this server.')
      })
  }, [])

  // A shared permalink (?a=<article_id>) loads the stored article.
  useEffect(() => {
    const shared = new URLSearchParams(window.location.search).get('a')
    if (!shared) return
    openStored(shared)
  }, [openStored])

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next = current === 'dark' ? 'light' : 'dark'
      localStorage.setItem(THEME_KEY, next)
      return next
    })
  }, [])

  const handleUrlChange = useCallback((value: string) => {
    setUrl(value)
    window.clearTimeout(lookupTimer.current)
    const candidate = value.trim()
    if (candidate.length < 4 || !candidate.includes('.')) {
      setLookup(null)
      return
    }
    lookupTimer.current = window.setTimeout(() => {
      fetchLookup(candidate.includes('://') ? candidate : `https://${candidate}`)
        .then(setLookup)
        .catch(() => setLookup(null))
    }, 350)
  }, [])

  const pushHistory = useCallback((article: Article) => {
    setHistory((current) => {
      const entry: HistoryEntry = {
        url: article.url,
        title: article.title,
        site: article.site,
        at: Date.now(),
      }
      const next = [entry, ...current.filter((item) => item.url !== article.url)].slice(
        0,
        MAX_HISTORY,
      )
      localStorage.setItem(HISTORY_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  const run = useCallback(
    async (targetUrl: string, opts: FetchOptions) => {
      if (!targetUrl.trim()) return

      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      setMode('link')
      setPhase('fetching')
      setError('')
      setJob(null)
      setElapsed(0)
      startedAt.current = Date.now()

      const ticker = window.setInterval(() => {
        setElapsed(Date.now() - startedAt.current)
      }, 100)

      try {
        const started = await startFetch(targetUrl, opts)
        setUrl(started.url)
        const finished = await waitForJob(
          started.job_id,
          (update) => setJob(update),
          controller.signal,
        )
        setJob(finished)
        if (finished.status === 'done' && finished.result) {
          setPhase('done')
          pushHistory(finished.result.article)
          if (finished.result.article_id) {
            const next = new URL(window.location.href)
            next.searchParams.set('a', finished.result.article_id)
            window.history.replaceState({}, '', next)
          }
          window.setTimeout(() => {
            document.querySelector('.reader')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }, 60)
        } else {
          setPhase('error')
          setError(finished.error?.message ?? 'The fetch failed.')
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') return
        setPhase('error')
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        window.clearInterval(ticker)
        setElapsed(Date.now() - startedAt.current)
      }
    },
    [pushHistory],
  )

  const reset = useCallback(() => {
    abortRef.current?.abort()
    const next = new URL(window.location.href)
    next.searchParams.delete('a')
    window.history.replaceState({}, '', next)
    setPhase('idle')
    setJob(null)
    setError('')
    setUrl('')
    setLookup(null)
    setMode('link')
  }, [])

  const busy = phase === 'fetching'
  const result = useMemo(() => (job?.status === 'done' ? job.result : null), [job])

  const form = (
    <UrlForm
      url={url}
      onUrlChange={handleUrlChange}
      onSubmit={run}
      onOpenSites={() => setSitesOpen(true)}
      busy={busy}
      lookup={lookup}
      options={options}
      onOptionsChange={setOptions}
    />
  )

  const modeTabs = (
    <div className="mode-tabs" role="tablist" aria-label="Fetch mode">
      {MODES.map((tab) => (
        <button
          key={tab.key}
          role="tab"
          aria-selected={mode === tab.key}
          className={mode === tab.key ? 'is-active' : ''}
          onClick={() => {
            setMode(tab.key)
            if (tab.key !== 'link') {
              setPhase('idle')
              setJob(null)
              setError('')
            }
          }}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )

  return (
    <div className="app">
      <div className="shell">
        <Header
          theme={theme}
          onToggleTheme={toggleTheme}
          onOpenSites={() => setSitesOpen(true)}
          onOpenLibrary={() => setLibraryOpen(true)}
          onOpenStats={() => setStatsOpen(true)}
          siteCount={info?.sites_supported ?? null}
          onReset={reset}
          compact={phase !== 'idle'}
        />

        <main className={phase === 'idle' && mode === 'link' ? 'stage stage-idle' : 'stage'}>
          {phase === 'idle' && mode === 'link' && (
            <section className="hero">
              <p className="hero-kicker">
                <span className="kicker-rule" />
                A reader for the articles you can’t quite reach
              </p>
              <h1 className="hero-title">
                Read the <em>whole</em> story.
              </h1>
              <p className="hero-deck">
                Paste a link. Offprint knocks the way a search engine would, renders whatever
                JavaScript is left, and hands you the article as clean Markdown — images, headings
                and all.
              </p>

              {modeTabs}
              {form}

              <ul className="how-it-works">
                {[
                  ['01', 'Publisher headers', 'The User-Agent and Referer that each of 936 sites expects.'],
                  ['02', 'Crawler identity', 'What Google sees, for sites that whitelist crawlers.'],
                  ['03', 'Headless Chromium', 'Paywall scripts aborted, overlays removed, DOM read.'],
                  ['04', 'Markdown out', 'Extraction with metered copy and site chrome stripped.'],
                ].map(([index, title, body]) => (
                  <li key={index}>
                    <span className="how-index">{index}</span>
                    <span className="how-title">{title}</span>
                    <span className="how-body">{body}</span>
                  </li>
                ))}
              </ul>

              {history.length > 0 && (
                <HistoryStrip
                  entries={history}
                  onPick={(entry) => {
                    setUrl(entry.url)
                    void run(entry.url, options)
                  }}
                  onClear={() => {
                    setHistory([])
                    localStorage.removeItem(HISTORY_KEY)
                  }}
                />
              )}

              {info && !info.playwright_ready && (
                <p className="notice notice-warn hero-notice">
                  <Icon name="bolt" size={14} />
                  Chromium isn’t installed, so JS-rendered paywalls will fail. Run{' '}
                  <code>uv run playwright install chromium</code>.
                </p>
              )}
            </section>
          )}

          {phase === 'idle' && mode !== 'link' && (
            <section className="hero hero-tool">
              <div className="tool-head">
                {modeTabs}
                <h1 className="tool-title">
                  {mode === 'batch' ? 'A stack of links, one pass.' : 'What did they publish today?'}
                </h1>
                <p className="tool-deck">
                  {mode === 'batch'
                    ? 'Paste up to 50 article URLs. They resolve in parallel, honouring the same cache and rate limits as a single fetch.'
                    : 'Pick a publication and Offprint reads its own RSS feed or sitemap — then you choose what to fetch.'}
                </p>
              </div>

              {mode === 'batch' ? (
                <BatchPanel
                  options={options}
                  onOpenArticle={openStored}
                  onOpenUrl={(target) => {
                    setUrl(target)
                    void run(target, options)
                  }}
                />
              ) : (
                <DiscoverPanel
                  initialSite={discoverSite}
                  onFetchUrl={(target) => {
                    setUrl(target)
                    void run(target, options)
                  }}
                  onFetchMany={(urls) => {
                    setPendingBatch(urls)
                    setMode('batch')
                  }}
                />
              )}

              {mode === 'batch' && pendingBatch.length > 0 && (
                <p className="notice">
                  {pendingBatch.length} article{pendingBatch.length === 1 ? '' : 's'} queued from
                  discovery — paste or press Fetch all to run them.
                  <button
                    className="link-btn"
                    onClick={() => {
                      setPendingBatch([])
                    }}
                  >
                    dismiss
                  </button>
                </p>
              )}
            </section>
          )}

          {(phase !== 'idle' || mode === 'link') && phase !== 'idle' && (
            <section className="workspace">
              {modeTabs}
              {form}

              {busy && <FetchProgress steps={job?.steps ?? []} elapsed={elapsed} />}

              {phase === 'error' && (
                <div className="error-card">
                  <h2>
                    <Icon name="cross" size={16} /> That one didn’t come through
                  </h2>
                  <p className="error-message">{error}</p>
                  <div className="error-actions">
                    <button
                      className="tool-btn"
                      onClick={() =>
                        void run(url, { ...options, force_browser: true, skip_browser: false })
                      }
                    >
                      <Icon name="bolt" size={14} />
                      Retry with browser
                    </button>
                    <button className="tool-btn" onClick={reset}>
                      <Icon name="arrow" size={14} />
                      Start over
                    </button>
                  </div>
                </div>
              )}

              {result && <ArticleView result={result} jobId={job?.id || undefined} onNew={reset} />}
            </section>
          )}
        </main>

        <footer className="foot">
          <span>
            Offprint · {info ? `v${info.version}` : 'v0.1.0'} ·{' '}
            {(info?.sites_supported ?? 936).toLocaleString()} sites ·{' '}
            {(info?.articles_stored ?? 0).toLocaleString()} stored
          </span>
          <span className="foot-links">
            <a href="https://github.com/Sophomoresty/bpc-fetch" target="_blank" rel="noreferrer">
              built on bpc-fetch
            </a>
            <span className="foot-sep">·</span>
            <a href="/docs" target="_blank" rel="noreferrer">
              API
            </a>
            <span className="foot-sep">·</span>
            <span>read what you already pay for</span>
          </span>
        </footer>
      </div>

      <SitesModal
        open={sitesOpen}
        onClose={() => setSitesOpen(false)}
        onPick={(domain) => {
          setSitesOpen(false)
          setDiscoverSite(domain)
          setMode('latest')
          setPhase('idle')
        }}
      />
      <LibraryModal
        open={libraryOpen}
        onClose={() => setLibraryOpen(false)}
        onOpenArticle={(articleId) => {
          setLibraryOpen(false)
          openStored(articleId)
        }}
      />
      <StatsModal
        open={statsOpen}
        onClose={() => setStatsOpen(false)}
        siteCount={info?.sites_supported ?? null}
        sitesUpdatedAt={info?.sites_updated_at ?? ''}
      />
    </div>
  )
}
