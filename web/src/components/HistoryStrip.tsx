import { Icon } from './Icon'

interface Entry {
  url: string
  title: string
  site: string
  at: number
}

interface Props {
  entries: Entry[]
  onPick: (entry: Entry) => void
  onClear: () => void
}

function when(timestamp: number): string {
  const minutes = Math.round((Date.now() - timestamp) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function HistoryStrip({ entries, onPick, onClear }: Props) {
  return (
    <section className="history">
      <div className="history-head">
        <h2>
          <Icon name="clock" size={14} />
          Recently fetched
        </h2>
        <button className="link-btn" onClick={onClear}>
          Clear
        </button>
      </div>
      <ul className="history-list">
        {entries.map((entry) => (
          <li key={entry.url}>
            <button className="history-item" onClick={() => onPick(entry)} title={entry.url}>
              <span className="history-site">{entry.site}</span>
              <span className="history-title">{entry.title}</span>
              <span className="history-when">{when(entry.at)}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
