import { Icon } from './Icon'

interface Props {
  theme: 'light' | 'dark'
  onToggleTheme: () => void
  onOpenSites: () => void
  onOpenLibrary: () => void
  onOpenStats: () => void
  siteCount: number | null
  onReset: () => void
  compact: boolean
}

export function Header({
  theme,
  onToggleTheme,
  onOpenSites,
  onOpenLibrary,
  onOpenStats,
  siteCount,
  onReset,
  compact,
}: Props) {
  return (
    <header className="topbar">
      <button className="brand" onClick={onReset} title="Start over">
        <span className="brand-mark">off</span>
        <span className="brand-dot">·</span>
        <span className="brand-tail">print</span>
      </button>

      {compact && <span className="topbar-note">Markdown, images, the whole story</span>}

      <nav className="topbar-actions">
        <button className="ghost-btn" onClick={onOpenSites}>
          <Icon name="globe" size={15} />
          <span>Sites</span>
          {siteCount !== null && <span className="count-pill">{siteCount}</span>}
        </button>
        <button className="ghost-btn" onClick={onOpenLibrary}>
          <Icon name="book" size={15} />
          <span>Library</span>
        </button>
        <button className="ghost-btn" onClick={onOpenStats}>
          <Icon name="clock" size={15} />
          <span>Health</span>
        </button>
        <a className="ghost-btn" href="/docs" target="_blank" rel="noreferrer">
          <Icon name="code" size={15} />
          <span>API</span>
        </a>
        <button
          className="icon-btn"
          onClick={onToggleTheme}
          aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          title="Toggle theme"
        >
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={17} />
        </button>
      </nav>
    </header>
  )
}
