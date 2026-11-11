import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Article, FetchOptions, Health, Job, Lookup } from './types'
import { getArticle, health as fetchHealth, lookup as fetchLookup, startFetch, waitForJob } from './api'
import { ArticleView } from './components/ArticleView'
import { FetchProgress } from './components/FetchProgress'
import { Header } from './components/Header'
import { HistoryStrip } from './components/HistoryStrip'
import { SitesModal } from './components/SitesModal'
import { UrlForm } from './components/UrlForm'
import { Icon } from './components/Icon'

type Phase = 'idle' | 'fetching' | 'done' | 'error'
type HistoryEntry = { url: string; title: string; site: string; at: number }

const HISTORY_KEY = 'bpc-fetch-history'
const THEME_KEY = 'bpc-fetch-theme'
const MAX_HISTORY = 8

const DEFAULT_OPTIONS: FetchOptions = {
  force_browser: false,
  skip_browser: false,
  use_archive: true,
  include_images: true,
  proxy_fallback: true,
}

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
  const [url, setUrl] = useState('')
  const [options, setOptions] = useState<FetchOptions>(DEFAULT_OPTIONS)
  const [phase, setPhase] = useState<Phase>('idle')
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [lookup, setLookup] = useState<Lookup | null>(null)
  const [info, setInfo] = useState<Health | null>(null)
  const [sitesOpen, setSitesOpen] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[]>(readHistory)
  const [elapsed, setElapsed] = useState(0)

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

  // A shared permalink (?a=<article_id>) loads the stored article.
  useEffect(() => {
    const shared = new URLSearchParams(window.location.search).get('a')
    if (!shared) return
    let cancelled = false
    getArticle(shared)
      .then((stored) => {
        if (cancelled) return
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
      })
      .catch(() => {
        if (!cancelled) {
          setPhase('error')
          setError('That shared article is no longer stored on this server.')
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

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

  return (
    <div className="app">
      <div className="shell">
        <Header
          theme={theme}
          onToggleTheme={toggleTheme}
          onOpenSites={() => setSitesOpen(true)}
          siteCount={info?.sites_supported ?? null}
          onReset={reset}
          compact={phase !== 'idle'}
        />

        <main className={phase === 'idle' ? 'stage stage-idle' : 'stage'}>
          {phase === 'idle' ? (
            <section className="hero">
              <p className="hero-kicker">
                <span className="kicker-rule" />
                A reader for the articles you can’t quite reach
              </p>
              <h1 className="hero-title">
                Read the <em>whole</em> story.
              </h1>
              <p className="hero-deck">
                Paste a link. bpc-fetch knocks the way a search engine would, renders whatever
                JavaScript is left, and hands you the article as clean Markdown — images, headings
                and all.
              </p>

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
          ) : (
            <section className="workspace">
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
            bpc-fetch web · {info ? `v${info.version}` : 'v0.1.0'} ·{' '}
            {(info?.sites_supported ?? 936).toLocaleString()} sites
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
          handleUrlChange(`https://www.${domain}/`)
        }}
      />
    </div>
  )
}
