import { useState } from 'react'
import { injectScenario, triggerScenario } from '../services/api'
import type { DemoScenario } from '../types/events'
import type { InjectScenario } from '../services/api'

interface ScenarioButtonsProps {
  onTriggered: (correlationId: string) => void
}

// ── Demo scenarios (no cluster required) ─────────────────────────────────────
const INJECT_SCENARIOS: { id: InjectScenario; icon: string; label: string; description: string; color: string }[] = [
  {
    id: 'application',
    icon: '⚙️',
    label: 'App Crash',
    description: 'NullPointerException after bad deploy',
    color: '#e53935',
  },
  {
    id: 'network',
    icon: '🌐',
    label: 'Network Failure',
    description: 'DNS resolution failure for postgres',
    color: '#1e88e5',
  },
  {
    id: 'database',
    icon: '🗄️',
    label: 'DB Overload',
    description: 'Connection pool exhausted (100 max)',
    color: '#8e24aa',
  },
  {
    id: 'unknown',
    icon: '❓',
    label: 'Unknown',
    description: 'No diagnosis — manual triage required',
    color: '#757575',
  },
]

// ── K8s cluster scenarios (require kind + demo namespace) ────────────────────
const K8S_SCENARIOS: { id: DemoScenario; icon: string; label: string; description: string; color: string }[] = [
  { id: 'crash',      icon: '💥', label: 'Crash Loop',   description: '/panic → CrashLoopBackOff',       color: '#e53935' },
  { id: 'bad-deploy', icon: '📦', label: 'Bad Deploy',   description: 'v2 image with RUNTIME_ERROR=true', color: '#fb8c00' },
  { id: 'oom',        icon: '💾', label: 'OOM Kill',     description: '/stress?mem=50 with 32Mi limit',   color: '#8e24aa' },
  { id: 'scale',      icon: '📈', label: 'High Load',    description: 'bombardier → error rate spike',    color: '#1e88e5' },
]

type RunningKey = `inject-${InjectScenario}` | `k8s-${DemoScenario}` | null

export function ScenarioButtons({ onTriggered }: ScenarioButtonsProps) {
  const [running, setRunning] = useState<RunningKey>(null)
  const [lastError, setLastError] = useState<string | null>(null)

  async function handleInject(scenario: InjectScenario) {
    const key: RunningKey = `inject-${scenario}`
    if (running) return
    setRunning(key)
    setLastError(null)
    try {
      const resp = await injectScenario(scenario)
      onTriggered(resp.correlation_id)
    } catch (err: unknown) {
      setLastError(err instanceof Error ? err.message : 'Inject failed')
    } finally {
      setRunning(null)
    }
  }

  async function handleK8s(scenario: DemoScenario) {
    const key: RunningKey = `k8s-${scenario}`
    if (running) return
    setRunning(key)
    setLastError(null)
    try {
      const resp = await triggerScenario(scenario)
      onTriggered(resp.correlation_id)
    } catch (err: unknown) {
      setLastError(err instanceof Error ? err.message : 'Trigger failed')
    } finally {
      setRunning(null)
    }
  }

  return (
    <section className="panel">
      <h2 className="panel__title">Trigger Incident</h2>

      {lastError && <p className="error-msg">{lastError}</p>}

      {/* Demo scenarios — no cluster required */}
      <p className="scenario-section-label">Demo scenarios <span className="badge badge--ok">no cluster needed</span></p>
      <div className="scenario-grid">
        {INJECT_SCENARIOS.map(s => {
          const key: RunningKey = `inject-${s.id}`
          return (
            <button
              key={s.id}
              className="scenario-btn"
              style={{ '--accent': s.color } as React.CSSProperties}
              disabled={running !== null}
              onClick={() => handleInject(s.id)}
              title={s.description}
            >
              <span className="scenario-btn__icon">{running === key ? <span className="spinner" /> : s.icon}</span>
              <span className="scenario-btn__label">{s.label}</span>
              <span className="scenario-btn__desc">{s.description}</span>
            </button>
          )
        })}
      </div>

      {/* K8s scenarios — require kind cluster */}
      <p className="scenario-section-label" style={{ marginTop: 16 }}>
        K8s scenarios <span className="badge badge--warn">requires cluster</span>
      </p>
      <div className="scenario-grid">
        {K8S_SCENARIOS.map(s => {
          const key: RunningKey = `k8s-${s.id}`
          return (
            <button
              key={s.id}
              className="scenario-btn scenario-btn--secondary"
              style={{ '--accent': s.color } as React.CSSProperties}
              disabled={running !== null}
              onClick={() => handleK8s(s.id)}
              title={s.description}
            >
              <span className="scenario-btn__icon">{running === key ? <span className="spinner" /> : s.icon}</span>
              <span className="scenario-btn__label">{s.label}</span>
              <span className="scenario-btn__desc">{s.description}</span>
            </button>
          )
        })}
      </div>
    </section>
  )
}
