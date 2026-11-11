import type { Step } from '../types'
import { Icon } from './Icon'

const STAGE_LABELS: Record<string, string> = {
  resolve: 'Identify site',
  http: 'Direct request',
  bot: 'Crawler User-Agent',
  browser: 'Headless browser',
  archive: 'Wayback Machine',
  cache: 'Cache',
}

const ORDER = ['resolve', 'http', 'bot', 'browser', 'archive', 'cache']

export interface StageState {
  stage: string
  status: 'pending' | 'running' | 'done' | 'failed'
  label: string
}

export function collapseSteps(steps: Step[]): StageState[] {
  const byStage = new Map<string, StageState>()
  for (const step of steps) {
    const status =
      step.status === 'running' ? 'running' : step.status === 'failed' ? 'failed' : 'done'
    const existing = byStage.get(step.stage)
    if (!existing) {
      byStage.set(step.stage, { stage: step.stage, status, label: step.label })
    } else {
      existing.status = status
      existing.label = step.label
    }
  }
  return [...byStage.values()].sort(
    (a, b) => ORDER.indexOf(a.stage) - ORDER.indexOf(b.stage),
  )
}

export function FetchProgress({ steps, elapsed }: { steps: Step[]; elapsed: number }) {
  const stages = collapseSteps(steps)
  const active = stages.find((stage) => stage.status === 'running')

  return (
    <section className="progress-card" aria-live="polite">
      <div className="progress-head">
        <span className="progress-title">
          <span className="pulse" />
          {active ? active.label : 'Working…'}
        </span>
        <span className="progress-time">{(elapsed / 1000).toFixed(1)}s</span>
      </div>

      <ol className="timeline">
        {stages.map((stage) => (
          <li key={stage.stage} className={`timeline-item is-${stage.status}`}>
            <span className="timeline-dot">
              {stage.status === 'done' && <Icon name="check" size={11} />}
              {stage.status === 'failed' && <Icon name="cross" size={11} />}
              {stage.status === 'running' && <span className="dot-spin" />}
            </span>
            <span className="timeline-stage">{STAGE_LABELS[stage.stage] ?? stage.stage}</span>
            <span className="timeline-label">{stage.label}</span>
          </li>
        ))}
      </ol>

      <p className="progress-note">
        Strategies escalate until one lands: publisher headers → crawler identity → rendered
        Chromium with the paywall scripts blocked → archived snapshot.
      </p>
    </section>
  )
}
