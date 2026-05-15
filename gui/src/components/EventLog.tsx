import { useEffect, useRef } from 'react'
import type { WorkflowEvent } from '../types/events'

interface EventLogProps {
  events: WorkflowEvent[]
  connected: boolean
  correlationId: string | null
}

const TYPE_COLORS: Record<string, string> = {
  node_started:       '#64b5f6',
  node_completed:     '#81c784',
  node_failed:        '#e57373',
  awaiting_approval:  '#ffb74d',
  approved:           '#4caf50',
  rejected:           '#ef5350',
  expired:            '#bdbdbd',
  solver_done:        '#4caf50',
  run_failed:         '#f44336',
  reconnect_missed:   '#ff7043',
}

function formatTs(iso: string): string {
  try {
    return new Date(iso).toISOString().slice(11, 23)
  } catch {
    return iso
  }
}

export function EventLog({ events, connected, correlationId }: EventLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  return (
    <section className="panel panel--log">
      <h2 className="panel__title">
        Event Log
        {connected && (
          <span className="live-badge">● LIVE</span>
        )}
        {correlationId && (
          <span className="cid-badge" title={correlationId}>
            {correlationId.slice(0, 12)}…
          </span>
        )}
      </h2>

      <div className="event-log">
        {events.length === 0 && (
          <p className="muted">No events yet. Trigger a scenario to begin.</p>
        )}
        {events.map(ev => (
          <div key={`${ev.seq}`} className="event-row">
            <span className="event-ts">{formatTs(ev.ts)}</span>
            <span
              className="event-type"
              style={{ color: TYPE_COLORS[ev.type] ?? '#e0e0e0' }}
            >
              {ev.type}
            </span>
            {ev.node && <span className="event-node">({ev.node})</span>}
            {Object.keys(ev.data).length > 0 && (
              <span className="event-data">
                {JSON.stringify(ev.data)}
              </span>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </section>
  )
}
