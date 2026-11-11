import { useEffect, useRef, useState } from 'react'
import type { SitesResponse } from '../types'
import { searchSites } from '../api'
import { Icon } from './Icon'

interface Props {
  open: boolean
  onClose: () => void
  onPick: (domain: string) => void
}

const QUICK = ['nytimes.com', 'economist.com', 'ft.com', 'wsj.com', 'spiegel.de', 'lemonde.fr', 'elpais.com', 'theatlantic.com']

export function SitesModal({ open, onClose, onPick }: Props) {
  const [query, setQuery] = useState('')
  const [data, setData] = useState<SitesResponse | null>(null)
  const [loading, setLoading] = useState(false)
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
    let cancelled = false
    setLoading(true)
    const timer = window.setTimeout(() => {
      searchSites(query, 80)
        .then((response) => {
          if (!cancelled) setData(response)
        })
        .catch(() => {
          if (!cancelled) setData(null)
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }, 180)
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
        aria-label="Supported sites"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <div>
            <h2>Supported sites</h2>
            <p className="modal-sub">
              {data ? `${data.supported_total} domains` : 'Loading…'} with a known paywall strategy
              — from the Bypass Paywalls Clean table.
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
            placeholder="Search a publication or domain…"
            spellCheck={false}
          />
          {loading && <Icon name="spinner" size={15} className="spin" />}
        </div>

        <div className="quick-row">
          {QUICK.map((domain) => (
            <button key={domain} className="quick-chip" onClick={() => onPick(domain)}>
              {domain}
            </button>
          ))}
        </div>

        <ul className="site-list">
          {data?.sites.map((site) => (
            <li key={site.domain}>
              <button className="site-row" onClick={() => onPick(site.domain)}>
                <span className="site-name">{site.name || site.domain}</span>
                <span className="site-domain">{site.domain}</span>
                <span className={`site-strategy strat-${site.bypass_type.split(':')[0]}`}>
                  {site.bypass_type}
                </span>
              </button>
            </li>
          ))}
          {data && data.sites.length === 0 && (
            <li className="site-empty">
              No match. Unlisted sites still work through the generic path.
            </li>
          )}
        </ul>

        {data && data.total > data.sites.length && (
          <p className="modal-foot">
            Showing {data.sites.length} of {data.total.toLocaleString()} matches — refine the
            search to narrow it down.
          </p>
        )}
      </div>
    </div>
  )
}
