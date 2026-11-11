import { useEffect, useRef, useState } from 'react'
import type { FetchOptions, Lookup } from '../types'
import { strategyLabel } from '../lib/format'
import { Icon } from './Icon'

interface Props {
  url: string
  onUrlChange: (value: string) => void
  onSubmit: (url: string, options: FetchOptions) => void
  onOpenSites: () => void
  busy: boolean
  lookup: Lookup | null
  options: FetchOptions
  onOptionsChange: (options: FetchOptions) => void
}

const OPTION_LABELS: Array<{ key: keyof FetchOptions; label: string; hint: string }> = [
  { key: 'force_browser', label: 'Force browser', hint: 'Render with Chromium even if HTTP worked' },
  { key: 'skip_browser', label: 'No browser', hint: 'Fastest: never launch Chromium' },
  { key: 'use_archive', label: 'Wayback', hint: 'Fall back to archive.org snapshots' },
  { key: 'include_images', label: 'Images', hint: 'Keep images in the Markdown' },
]

export function UrlForm({
  url,
  onUrlChange,
  onSubmit,
  onOpenSites,
  busy,
  lookup,
  options,
  onOptionsChange,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [showOptions, setShowOptions] = useState(false)
  const [pasteError, setPasteError] = useState('')

  useEffect(() => {
    if (!busy) inputRef.current?.focus()
  }, [busy])

  async function handlePaste() {
    try {
      const text = await navigator.clipboard.readText()
      if (text.trim()) {
        onUrlChange(text.trim())
        setPasteError('')
      }
    } catch {
      setPasteError('Clipboard blocked — paste with ⌘V instead.')
      inputRef.current?.focus()
    }
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!busy) onSubmit(url, options)
  }

  const toggle = (key: keyof FetchOptions) => {
    const next = { ...options, [key]: !options[key] }
    if (key === 'force_browser' && next.force_browser) next.skip_browser = false
    if (key === 'skip_browser' && next.skip_browser) next.force_browser = false
    onOptionsChange(next)
  }

  return (
    <form className="fetch-form" onSubmit={handleSubmit}>
      <div className={`fetch-bar${busy ? ' is-busy' : ''}`}>
        <span className="fetch-bar-icon" aria-hidden="true">
          <Icon name="globe" size={18} />
        </span>
        <input
          ref={inputRef}
          className="fetch-input"
          type="text"
          inputMode="url"
          spellCheck={false}
          autoComplete="off"
          placeholder="Paste an article link — nytimes.com, ft.com, economist.com…"
          value={url}
          onChange={(event) => onUrlChange(event.target.value)}
          aria-label="Article URL"
        />
        {url && !busy && (
          <button
            type="button"
            className="clear-btn"
            onClick={() => {
              onUrlChange('')
              inputRef.current?.focus()
            }}
            aria-label="Clear URL"
          >
            <Icon name="close" size={14} />
          </button>
        )}
        <button type="button" className="paste-btn" onClick={handlePaste} title="Paste from clipboard">
          Paste
        </button>
        <button className="primary-btn" type="submit" disabled={busy || !url.trim()}>
          {busy ? (
            <>
              <Icon name="spinner" size={16} className="spin" />
              <span>Fetching</span>
            </>
          ) : (
            <>
              <span>Fetch</span>
              <Icon name="arrow" size={16} />
            </>
          )}
        </button>
      </div>

      <div className="fetch-meta">
        <div className="fetch-meta-left">
          {lookup?.supported ? (
            <span className={`badge badge-${lookup.bypass_type.split(':')[0]}`}>
              <Icon name="bolt" size={12} />
              {lookup.name || lookup.domain}
              <em>{strategyLabel(lookup.bypass_type)}</em>
            </span>
          ) : lookup && !lookup.supported ? (
            <span className="badge badge-generic">
              <Icon name="globe" size={12} />
              {lookup.domain}
              <em>generic path</em>
            </span>
          ) : (
            <span className="hint-text">
              936 paywalled titles supported · <button type="button" className="link-btn" onClick={onOpenSites}>browse the list</button>
            </span>
          )}
          {pasteError && <span className="hint-error">{pasteError}</span>}
        </div>

        <button
          type="button"
          className={`options-toggle${showOptions ? ' is-open' : ''}`}
          onClick={() => setShowOptions((value) => !value)}
          aria-expanded={showOptions}
        >
          Options
          <Icon name="arrow" size={13} />
        </button>
      </div>

      {showOptions && (
        <div className="options-row">
          {OPTION_LABELS.map(({ key, label, hint }) => (
            <button
              key={key}
              type="button"
              className={`toggle${options[key] ? ' is-on' : ''}`}
              onClick={() => toggle(key)}
              aria-pressed={options[key]}
              title={hint}
            >
              <span className="toggle-dot" />
              {label}
            </button>
          ))}
        </div>
      )}
    </form>
  )
}
