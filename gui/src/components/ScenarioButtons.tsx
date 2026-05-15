import { useState } from 'react'
import { triggerScenario } from '../services/api'
import type { DemoScenario } from '../types/events'

interface ScenarioButtonsProps {
  onTriggered: (correlationId: string) => void
}

const SCENARIOS: { id: DemoScenario; label: string; description: string; color: string }[] = [
  {
    id: 'crash',
    label: 'Crash Loop',
    description: 'Calls /panic → CrashLoopBackOff',
    color: '#e53935',
  },
  {
    id: 'bad-deploy',
    label: 'Bad Deploy',
    description: 'Applies v2 image with RUNTIME_ERROR=true',
    color: '#fb8c00',
  },
  {
    id: 'oom',
    label: 'OOM Kill',
    description: '/stress?mem=50 with 32Mi limit',
    color: '#8e24aa',
  },
  {
    id: 'scale',
    label: 'High Load',
    description: 'bombardier in-cluster → error rate spike',
    color: '#1e88e5',
  },
]

export function ScenarioButtons({ onTriggered }: ScenarioButtonsProps) {
  const [running, setRunning] = useState<DemoScenario | null>(null)
  const [lastError, setLastError] = useState<string | null>(null)

  async function handleClick(scenario: DemoScenario) {
    if (running) return
    setRunning(scenario)
    setLastError(null)
    try {
      const resp = await triggerScenario(scenario)
      onTriggered(resp.correlation_id)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Trigger failed'
      setLastError(msg)
    } finally {
      setRunning(null)
    }
  }

  return (
    <section className="panel">
      <h2 className="panel__title">Trigger Failure Scenario</h2>
      {lastError && <p className="error-msg">{lastError}</p>}
      <div className="scenario-grid">
        {SCENARIOS.map(s => (
          <button
            key={s.id}
            className="scenario-btn"
            style={{ '--accent': s.color } as React.CSSProperties}
            disabled={running !== null}
            onClick={() => handleClick(s.id)}
            title={s.description}
          >
            {running === s.id ? (
              <span className="spinner" />
            ) : null}
            <span className="scenario-btn__label">{s.label}</span>
            <span className="scenario-btn__desc">{s.description}</span>
          </button>
        ))}
      </div>
    </section>
  )
}
