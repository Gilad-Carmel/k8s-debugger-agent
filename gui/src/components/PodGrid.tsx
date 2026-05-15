import type { PodStatus } from '../types/events'

interface PodGridProps {
  pods: PodStatus[]
  loading: boolean
  error: string | null
}

function PodCard({ pod }: { pod: PodStatus }) {
  const isDown = !pod.ready || pod.phase === 'Failed' || pod.phase === 'Unknown'
  const isCrashing = pod.restart_count > 0

  return (
    <div className={`pod-card ${isDown ? 'pod-card--down' : 'pod-card--up'}`}>
      <div className="pod-card__header">
        <span className="pod-card__name" title={pod.name}>
          {pod.name.length > 24 ? `…${pod.name.slice(-21)}` : pod.name}
        </span>
        {isDown ? (
          <span className="pod-badge pod-badge--down" title="Pod is down">✕</span>
        ) : (
          <span className="pod-badge pod-badge--up" title="Pod is running">✓</span>
        )}
      </div>

      <div className="pod-card__meta">
        <span className={`pod-phase pod-phase--${pod.phase.toLowerCase()}`}>{pod.phase}</span>
        <span className="pod-ns">{pod.namespace}</span>
      </div>

      {isCrashing && (
        <div className="pod-card__restarts">
          🔄 Restarts: <strong>{pod.restart_count}</strong>
        </div>
      )}

      {pod.message && (
        <div className="pod-card__message" title={pod.message}>
          {pod.message}
        </div>
      )}
    </div>
  )
}

export function PodGrid({ pods, loading, error }: PodGridProps) {
  return (
    <section className="panel">
      <h2 className="panel__title">
        Pod Status
        {loading && <span className="spinner" />}
      </h2>

      {error && <p className="error-msg">{error}</p>}

      {pods.length === 0 && !loading && !error && (
        <p className="muted">No pods found in the demo namespace. Run <code>make demo-deploy</code>.</p>
      )}

      <div className="pod-grid">
        {pods.map(pod => (
          <PodCard key={`${pod.namespace}/${pod.name}`} pod={pod} />
        ))}
      </div>
    </section>
  )
}
