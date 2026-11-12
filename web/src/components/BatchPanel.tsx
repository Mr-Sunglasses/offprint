import { useEffect, useRef, useState } from 'react'
import type { BatchStatus, FetchOptions } from '../types'
import { getBatch, startBatch } from '../api'
import { Icon } from './Icon'

interface Props {
  options: FetchOptions
  onOpenArticle: (articleId: string) => void
  onOpenUrl: (url: string) => void
}

const PLACEHOLDER = `https://www.nytimes.com/2026/11/11/world/…
https://www.ft.com/content/…
https://www.spiegel.de/politik/…

One link per line — up to 50.`

export function BatchPanel({ options, onOpenArticle, onOpenUrl }: Props) {
  const [text, setText] = useState('')
  const [batch, setBatch] = useState<BatchStatus | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [skipped, setSkipped] = useState<Array<{ url: string; error: string }>>([])
  const timer = useRef<number | undefined>(undefined)

  const urls = text
    .split(/[\n,\s]+/)
    .map((value) => value.trim())
    .filter((value) => value.length > 3 && !value.startsWith('#'))

  useEffect(() => () => window.clearInterval(timer.current), [])

  async function submit() {
    if (!urls.length || busy) return
    setBusy(true)
    setError('')
    setSkipped([])
    setBatch(null)
    try {
      const created = await startBatch(urls, options)
      setSkipped(created.skipped ?? [])
      timer.current = window.setInterval(async () => {
        try {
          const next = await getBatch(created.id)
          setBatch(next)
          if (next.status === 'done') {
            window.clearInterval(timer.current)
            setBusy(false)
          }
        } catch {
          window.clearInterval(timer.current)
          setBusy(false)
          setError('Lost track of that batch — the server may have restarted.')
        }
      }, 900)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  const progress = batch ? Math.round((100 * (batch.done + batch.failed)) / Math.max(batch.total, 1)) : 0

  return (
    <section className="batch">
      <div className="batch-input-wrap">
        <textarea
          className="batch-input"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={PLACEHOLDER}
          spellCheck={false}
          rows={5}
          disabled={busy}
        />
        <div className="batch-actions">
          <span className="hint-text">
            {batch
              ? batch.status === 'done'
                ? `Batch finished · ${batch.done} ok, ${batch.failed} failed`
                : `Running · ${batch.done + batch.failed}/${batch.total}`
              : urls.length
                ? `${urls.length} link${urls.length === 1 ? '' : 's'} queued`
                : 'Paste links, one per line'}
          </span>
          <button className="primary-btn" onClick={submit} disabled={busy || !urls.length}>
            {busy ? (
              <>
                <Icon name="spinner" size={15} className="spin" />
                Fetching {batch ? `${batch.done + batch.failed}/${batch.total}` : '…'}
              </>
            ) : (
              <>
                Fetch all
                <Icon name="arrow" size={15} />
              </>
            )}
          </button>
        </div>
      </div>

      {error && <p className="notice notice-warn">{error}</p>}

      {skipped.length > 0 && (
        <p className="notice notice-warn">
          Skipped {skipped.length} invalid link{skipped.length === 1 ? '' : 's'}:{' '}
          {skipped.map((item) => item.url).join(', ')}
        </p>
      )}

      {batch && (
        <div className="batch-results">
          <div className="batch-progress">
            <div className="batch-progress-bar">
              <span style={{ width: `${progress}%` }} />
            </div>
            <span className="batch-progress-label">
              {batch.done} done · {batch.failed} failed · {batch.total} total
            </span>
          </div>

          <ul className="batch-list">
            {batch.items.map((item) => (
              <li key={item.job_id} className={`batch-item is-${item.status}`}>
                <span className={`batch-dot is-${item.status}`} />
                <div className="batch-item-main">
                  <button
                    className="batch-item-title"
                    disabled={!item.article_id}
                    onClick={() => item.article_id && onOpenArticle(item.article_id)}
                  >
                    {item.title || item.url}
                  </button>
                  <span className="batch-item-meta">
                    {item.status === 'done'
                      ? [
                          item.site,
                          item.words ? `${item.words.toLocaleString()} words` : '',
                          item.via,
                          item.preview ? 'preview' : '',
                          item.cached ? 'cached' : '',
                        ]
                          .filter(Boolean)
                          .join(' · ')
                      : item.status === 'error'
                        ? item.error?.slice(0, 110)
                        : item.steps || 'queued'}
                  </span>
                </div>
                <button className="batch-item-action" onClick={() => onOpenUrl(item.url)} title="Fetch this one on its own">
                  <Icon name="external" size={13} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
