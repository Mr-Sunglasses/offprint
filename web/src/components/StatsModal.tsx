import { useEffect, useState } from 'react'
import type { DomainStat } from '../types'
import { domainStats, updateSiteTable } from '../api'
import { Icon } from './Icon'

interface Props {
  open: boolean
  onClose: () => void
  siteCount: number | null
  sitesUpdatedAt: string
}

export function StatsModal({ open, onClose, siteCount, sitesUpdatedAt }: Props) {
  const [domains, setDomains] = useState<DomainStat[]>([])
  const [stored, setStored] = useState(0)
  const [loading, setLoading] = useState(false)
  const [updateState, setUpdateState] = useState('')
  const [updating, setUpdating] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    domainStats(60)
      .then((response) => {
        setDomains(response.domains)
        setStored(response.articles_stored)
      })
      .catch(() => setDomains([]))
      .finally(() => setLoading(false))

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  async function refreshTable(force: boolean) {
    setUpdating(true)
    setUpdateState('')
    try {
      const result = (await updateSiteTable(force)) as {
        updated?: boolean
        domains?: number
        error?: string
        reason?: string
      }
      if (result.error) setUpdateState(`Failed: ${result.error}`)
      else if (result.updated) setUpdateState(`Updated — ${result.domains} domains loaded`)
      else setUpdateState(result.reason ? `No change (${result.reason})` : 'Already current')
    } catch (err) {
      setUpdateState(err instanceof Error ? err.message : String(err))
    } finally {
      setUpdating(false)
    }
  }

  if (!open) return null

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal modal-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Domain health"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <div>
            <h2>What works, per domain</h2>
            <p className="modal-sub">
              Which stage actually lands each publisher, from this server&apos;s own history.
              {siteCount ? ` ${siteCount} sites in the table.` : ''}
            </p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="close" size={16} />
          </button>
        </div>

        <div className="stats-toolbar">
          <span className="stats-fact">
            {stored.toLocaleString()} articles stored
            {sitesUpdatedAt ? ` · table checked ${sitesUpdatedAt.slice(0, 10)}` : ' · table never updated'}
          </span>
          <div className="discover-head-actions">
            <button className="tool-btn" onClick={() => void refreshTable(false)} disabled={updating}>
              {updating ? <Icon name="spinner" size={14} className="spin" /> : <Icon name="clock" size={14} />}
              Check for table update
            </button>
            <button className="tool-btn" onClick={() => void refreshTable(true)} disabled={updating}>
              <Icon name="bolt" size={14} />
              Force
            </button>
          </div>
        </div>

        {updateState && <p className="notice">{updateState}</p>}

        {loading ? (
          <p className="site-empty">Loading…</p>
        ) : domains.length === 0 ? (
          <p className="site-empty">
            No outcomes recorded yet — fetch a few articles and this table fills in.
          </p>
        ) : (
          <div className="stats-table-wrap">
            <table className="stats-table">
              <thead>
                <tr>
                  <th>Domain</th>
                  <th>Tries</th>
                  <th>Won</th>
                  <th>Win rate</th>
                  <th>Best stage</th>
                  <th>Stages seen</th>
                </tr>
              </thead>
              <tbody>
                {domains.map((row) => (
                  <tr key={row.domain}>
                    <td className="stats-domain">{row.domain}</td>
                    <td>{row.tries}</td>
                    <td>{row.wins}</td>
                    <td>
                      <span className={`rate rate-${rateBucket(row.success_rate)}`}>
                        {row.success_rate}%
                      </span>
                    </td>
                    <td className="stats-stage">{row.best_stage || '—'}</td>
                    <td className="stats-stages">
                      {row.stages
                        .slice(0, 4)
                        .map((stage) => `${stage.stage} ${stage.wins}/${stage.tries}`)
                        .join(' · ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function rateBucket(rate: number): 'good' | 'mid' | 'bad' {
  if (rate >= 80) return 'good'
  if (rate >= 40) return 'mid'
  return 'bad'
}
