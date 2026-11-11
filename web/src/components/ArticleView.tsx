import { useMemo, useState } from 'react'
import type { FetchResult } from '../types'
import { markdownUrl } from '../api'
import { hostOf } from '../lib/format'
import { copyText, renderMarkdown } from '../lib/markdown'
import { Icon } from './Icon'

interface Props {
  result: FetchResult
  jobId: string
  onNew: () => void
}

function prettyDate(value: string): string {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
}

export function ArticleView({ result, jobId, onNew }: Props) {
  const { article } = result
  const [view, setView] = useState<'reader' | 'raw'>('reader')
  const [copied, setCopied] = useState(false)

  const rendered = useMemo(
    () => (view === 'reader' ? renderMarkdown(article.markdown, article.url) : null),
    [article.markdown, article.url, view],
  )

  const truncated = result.confidence === 'low' || result.via.includes('partial')

  async function handleCopy() {
    await copyText(article.markdown)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1800)
  }

  return (
    <article className="reader">
      <header className="reader-head">
        <div className="reader-eyebrow">
          <span className="eyebrow-site">{article.site || hostOf(article.url)}</span>
          <span className="eyebrow-chip subtle">{result.via.replace(' (partial)', '')}</span>
          {result.cached && <span className="eyebrow-chip subtle">cached</span>}
        </div>

        <h1 className="reader-title">{article.title}</h1>

        <div className="reader-byline">
          {article.author && <span>{article.author}</span>}
          {article.date && <span>{prettyDate(article.date)}</span>}
          <span>{article.word_count.toLocaleString()} words</span>
          <span>{article.reading_minutes} min read</span>
          <span className="byline-time">fetched in {(result.elapsed_ms / 1000).toFixed(1)}s</span>
        </div>

        {truncated && (
          <p className="notice notice-warn">
            <Icon name="bolt" size={14} />
            This one looks truncated — the publisher is metering or hard-gating this article, so
            what you see may be the preview. Try <strong>Force browser</strong>, or read the
            original.
          </p>
        )}
      </header>

      <div className="reader-toolbar">
        <div className="toolbar-group">
          <button className="tool-btn" onClick={handleCopy}>
            <Icon name={copied ? 'check' : 'copy'} size={15} />
            {copied ? 'Copied' : 'Copy'}
          </button>
          <a className="tool-btn" href={markdownUrl(jobId)} download>
            <Icon name="download" size={15} />
            Download .md
          </a>
          <a className="tool-btn" href={article.url} target="_blank" rel="noreferrer">
            <Icon name="external" size={15} />
            Original
          </a>
        </div>

        <div className="toolbar-group">
          <div className="segmented" role="group" aria-label="View mode">
            <button
              className={view === 'reader' ? 'is-active' : ''}
              onClick={() => setView('reader')}
              aria-pressed={view === 'reader'}
            >
              Reader
            </button>
            <button
              className={view === 'raw' ? 'is-active' : ''}
              onClick={() => setView('raw')}
              aria-pressed={view === 'raw'}
            >
              Markdown
            </button>
          </div>
          <button className="tool-btn" onClick={onNew}>
            <Icon name="arrow" size={15} />
            New fetch
          </button>
        </div>
      </div>

      {view === 'reader' ? (
        <div
          className={`prose${rendered?.dropCap ? ' has-dropcap' : ''}`}
          dangerouslySetInnerHTML={{ __html: rendered?.html ?? '' }}
        />
      ) : (
        <pre className="raw-md">{article.markdown}</pre>
      )}

      <details className="trace">
        <summary>
          Fetch trace · {result.attempts.length} attempt
          {result.attempts.length === 1 ? '' : 's'} · {result.confidence} confidence
        </summary>
        <table className="trace-table">
          <thead>
            <tr>
              <th>Stage</th>
              <th>Result</th>
              <th>Chars</th>
              <th>ms</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {result.attempts.map((attempt) => (
              <tr key={`${attempt.stage}-${attempt.ms}`}>
                <td>{attempt.label}</td>
                <td>
                  <span className={`status-dot ${attempt.ok ? 'ok' : 'fail'}`} />
                  {attempt.status ? `HTTP ${attempt.status}` : attempt.ok ? 'ok' : 'failed'}
                </td>
                <td>{attempt.chars.toLocaleString()}</td>
                <td>{attempt.ms}</td>
                <td className="trace-note">
                  {attempt.note || (attempt.paywalled ? 'paywall markers' : '')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </article>
  )
}
