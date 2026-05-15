import { useEffect, useRef, useState } from 'react'
import { fetchPodLogs } from '../services/api'
import type { PodStatus } from '../types/events'

interface PodGridProps {
  pods: PodStatus[]
  loading: boolean
  error: string | null
  warning?: string | null
}

function PodLogsModal({ pod, onClose }: { pod: PodStatus; onClose: () => void }) {
  const [logs, setLogs] = useState<string | null>(null)
  const [warn, setWarn] = useState<string | null>(null)
  const [fetching, setFetching] = useState(true)
  const preRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    let cancelled = false
    fetchPodLogs(pod.namespace, pod.name).then(res => {
      if (cancelled) return
      setLogs(res.logs)
      setWarn(res.warning)
      setFetching(false)
    }).catch(err => {
      if (cancelled) return
      setWarn(err instanceof Error ? err.message : 'Failed to fetch logs')
      setFetching(false)
    })
    return () => { cancelled = true }
  }, [pod.namespace, pod.name])

  useEffect(() => {
    preRef.current?.scrollTo(0, preRef.current.scrollHeight)
  }, [logs])

  return (
    <div className="approval-overlay" onClick={onClose}>
      <div className="pod-logs-modal" onClick={e => e.stopPropagation()}>
        <div className="pod-logs-modal__header">
          <span className="pod-logs-modal__title">{pod.name}</span>
          <span className="pod-logs-modal__ns">{pod.namespace}</span>
          <button className="pod-logs-modal__close" onClick={onClose}>✕</button>
        </div>
        {warn && <p className="error-msg" style={{ padding: '0 16px' }}>{warn}</p>}
        {fetching && <p className="muted" style={{ padding: '8px 16px' }}>Loading logs…</p>}
        <pre ref={preRef} className="pod-logs-modal__body">
          {logs || (fetching ? '' : '(no logs)')}
        </pre>
      </div>
    </div>
  )
}

function PodCard({ pod, onClick }: { pod: PodStatus; onClick: () => void }) {
  const isDown = !pod.ready || pod.phase === 'Failed' || pod.phase === 'Unknown'
  const isCrashing = pod.restart_count > 0

  return (
    <button
      className={`pod-card ${isDown ? 'pod-card--down' : 'pod-card--up'}`}
      onClick={onClick}
      title="Click to view logs"
    >
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
    </button>
  )
}

export function PodGrid({ pods, loading, error, warning }: PodGridProps) {
  const [selectedPod, setSelectedPod] = useState<PodStatus | null>(null)

  return (
    <section className="panel">
      <h2 className="panel__title">
        Pod Status
        {loading && <span className="spinner" />}
      </h2>

      {error && <p className="error-msg">{error}</p>}
      {!error && warning && <p className="muted">{warning}</p>}

      {pods.length === 0 && !loading && !error && !warning && (
        <p className="muted">No pods found in the demo namespace.</p>
      )}

      <div className="pod-grid">
        {pods.map(pod => (
          <PodCard
            key={`${pod.namespace}/${pod.name}`}
            pod={pod}
            onClick={() => setSelectedPod(pod)}
          />
        ))}
      </div>

      {selectedPod && (
        <PodLogsModal pod={selectedPod} onClose={() => setSelectedPod(null)} />
      )}
    </section>
  )
}
