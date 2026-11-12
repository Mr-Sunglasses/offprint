import { useEffect, useRef, useState } from 'react'
import type { SearchHit } from '../types'
import { searchLibrary } from '../api'
import { Icon } from './Icon'

interface Props {
  open: boolean
  onClose: () => void
  onOpenArticle: (articleId: string) => void
}

export function LibraryModal({ open, onClose, onOpenArticle }: Props) {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [count, setCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    inputRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  useEffect(() => {
    if (!open) return
    if (query.trim().length < 2) {
      setHits([])
      setCount(null)
      return
    }
    let cancelled = false
    setLoading(true)
    const timer = window.setTimeout(() => {
      searchLibrary(query)
        .then((response) => {
          if (cancelled) return
          setHits(response.results)
          setCount(response.count)
          setError('')
        })
        .catch((err: Error) => {
          if (!cancelled) setError(err.message)
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }, 220)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [query, open])

  if (!open) return null

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="Library search"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <div>
            <h2>Library</h2>
            <p className="modal-sub">
              Full-text search over every article this server has stored — including the ones you
              fetched weeks ago.
            </p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="close" size={16} />
          </button>
        </div>

        <div className="modal-search">
          <Icon name="search" size={16} />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search the text of stored articles…"
            spellCheck={false}
          />
          {loading && <Icon name="spinner" size={15} className="spin" />}
        </div>

        {error && <p className="notice notice-warn">{error}</p>}

        <ul className="site-list">
          {hits.map((hit) => (
            <li key={hit.article_id}>
              <button className="library-row" onClick={() => onOpenArticle(hit.article_id)}>
                <span className="library-title">{hit.title || hit.url}</span>
                <span className="library-meta">
                  {hit.site} · {hit.fetched_at?.slice(0, 10)} · {hit.word_count.toLocaleString()} words
                  {hit.paywalled_preview ? ' · preview' : ''}
                </span>
                {hit.snippet && (
                  <span
                    className="library-snippet"
                    dangerouslySetInnerHTML={{ __html: sanitiseSnippet(hit.snippet) }}
                  />
                )}
              </button>
            </li>
          ))}
          {count === 0 && query.trim().length >= 2 && (
            <li className="site-empty">Nothing stored matches “{query}”. Fetch a few articles first.</li>
          )}
          {count === null && query.trim().length < 2 && (
            <li className="site-empty">Type at least two characters to search.</li>
          )}
        </ul>

        {count !== null && count > 0 && (
          <p className="modal-foot">
            {count} match{count === 1 ? '' : 'es'} · click one to reopen it
          </p>
        )}
      </div>
    </div>
  )
}

/** The API returns FTS5 snippets with <mark> tags and nothing else. */
function sanitiseSnippet(snippet: string): string {
  return snippet
    .replace(/[<>](?!\/?mark>)/g, '')
    .replace(/&(?!(amp|lt|gt|quot);)/g, '&amp;')
}
