import { useEffect, useRef } from 'react'
import type { WorkflowEvent } from '../types/events'

interface PipelineLogProps {
  events: WorkflowEvent[]
  correlationId: string | null
  connected: boolean
}

const NODE_LABEL: Record<string, string> = {
  ingest:             'INGEST',
  router:             'ROUTER',
  application_expert: 'APP EXPERT',
  network_expert:     'NET EXPERT',
  database_expert:    'DB EXPERT',
  reporter:           'REPORTER',
  solver:             'SOLVER',
}

function fmtTs(iso: string) {
  try { return new Date(iso).toISOString().slice(11, 23) } catch { return iso }
}

type LogLine = { ts: string; container: string; text: string }

function LogLines({ lines }: { lines: LogLine[] }) {
  if (!lines || lines.length === 0) return null
  return (
    <div className="pl-log-lines">
      {lines.map((l, i) => (
        <div key={i} className="pl-log-line">
          <span className="pl-log-ts">{fmtTs(l.ts)}</span>
          <span className="pl-log-ctr">[{l.container}]</span>
          <span className="pl-log-text">{l.text}</span>
        </div>
      ))}
    </div>
  )
}

function StageBlock({ ev }: { ev: WorkflowEvent }) {
  const ts = fmtTs(ev.ts)
  const d = ev.data
  const label = ev.node ? (NODE_LABEL[ev.node] ?? ev.node.toUpperCase()) : ''

  if (ev.type === 'node_started') {
    return (
      <div className="pl-row pl-row--starting">
        <span className="pl-ts">{ts}</span>
        <span className="pl-badge">{label}</span>
        <span className="pl-muted">running…</span>
      </div>
    )
  }

  if (ev.type === 'node_completed') {
    if (ev.node === 'ingest') {
      const lines = (d.log_lines as LogLine[]) ?? []
      return (
        <div className="pl-block pl-block--ok">
          <div className="pl-block__head">
            <span className="pl-ts">{ts}</span>
            <span className="pl-badge pl-badge--ingest">INGEST</span>
            <span className="pl-meta">
              {d.hit_count as number} error hits &nbsp;·&nbsp; {d.total_lines as number} total lines scanned
            </span>
          </div>
          <LogLines lines={lines} />
        </div>
      )
    }

    if (ev.node === 'router') {
      const cited = (d.cited_lines as LogLine[]) ?? []
      return (
        <div className="pl-block pl-block--ok">
          <div className="pl-block__head">
            <span className="pl-ts">{ts}</span>
            <span className="pl-badge pl-badge--router">ROUTER</span>
            <span className="pl-domain" data-domain={d.domain as string}>{d.domain as string}</span>
            <span className="pl-meta">confidence: {d.confidence as string}</span>
          </div>
          {cited.length > 0 && (
            <>
              <div className="pl-sublabel">Cited evidence:</div>
              <LogLines lines={cited} />
            </>
          )}
        </div>
      )
    }

    if (ev.node === 'application_expert' || ev.node === 'network_expert' || ev.node === 'database_expert') {
      const cited = (d.cited_lines as LogLine[]) ?? []
      const fix = d.proposed_fix as { action_type: string; namespace: string; pod: string; parameters?: Record<string, unknown> } | null
      return (
        <div className="pl-block pl-block--ok">
          <div className="pl-block__head">
            <span className="pl-ts">{ts}</span>
            <span className="pl-badge pl-badge--expert">{label}</span>
            <span className="pl-meta">confidence: {d.confidence as string}</span>
          </div>
          {d.root_cause && (
            <div className="pl-root-cause">
              <span className="pl-sublabel">Root cause: </span>{d.root_cause as string}
            </div>
          )}
          {cited.length > 0 && (
            <>
              <div className="pl-sublabel">Supporting evidence:</div>
              <LogLines lines={cited} />
            </>
          )}
          {fix && (
            <div className="pl-fix">
              <span className="pl-sublabel">Proposed fix: </span>
              <code>{fix.action_type}</code> on <code>{fix.namespace}/{fix.pod}</code>
            </div>
          )}
        </div>
      )
    }

    if (ev.node === 'reporter') {
      return (
        <div className="pl-block pl-block--ok">
          <div className="pl-block__head">
            <span className="pl-ts">{ts}</span>
            <span className="pl-badge pl-badge--reporter">REPORTER</span>
            <span className="pl-domain" data-domain={d.domain as string}>{d.domain as string}</span>
          </div>
          {d.root_cause && (
            <div className="pl-root-cause">
              <span className="pl-sublabel">Root cause: </span>{d.root_cause as string}
            </div>
          )}
          {d.proposed_fix_action && (
            <div className="pl-fix">
              <span className="pl-sublabel">Fix: </span>
              <code>{d.proposed_fix_action as string}</code> on{' '}
              <code>{d.proposed_fix_namespace as string}/{d.proposed_fix_pod as string}</code>
            </div>
          )}
        </div>
      )
    }

    if (ev.node === 'solver') {
      const outcomeClass = (d.outcome as string) === 'success' ? 'pl-block--ok' : 'pl-block--failed'
      return (
        <div className={`pl-block ${outcomeClass}`}>
          <div className="pl-block__head">
            <span className="pl-ts">{ts}</span>
            <span className="pl-badge pl-badge--solver">SOLVER</span>
            <span className="pl-outcome" data-outcome={d.outcome as string}>{(d.outcome as string).toUpperCase()}</span>
          </div>
          {d.action_type && (
            <div className="pl-fix"><span className="pl-sublabel">Action: </span><code>{d.action_type as string}</code></div>
          )}
          {d.reversal && (
            <div className="pl-fix"><span className="pl-sublabel">Reversal: </span>{d.reversal as string}</div>
          )}
          {d.error && <div className="pl-error">{d.error as string}</div>}
        </div>
      )
    }

    return null
  }

  if (ev.type === 'node_failed') {
    return (
      <div className="pl-block pl-block--failed">
        <div className="pl-block__head">
          <span className="pl-ts">{ts}</span>
          <span className="pl-badge">{label}</span>
          <span className="pl-badge pl-badge--fail">FAILED</span>
        </div>
        {d.error && <div className="pl-error">{d.error as string}</div>}
      </div>
    )
  }

  if (ev.type === 'awaiting_approval') {
    return (
      <div className="pl-block pl-block--waiting">
        <div className="pl-block__head">
          <span className="pl-ts">{ts}</span>
          <span className="pl-badge pl-badge--hitl">HITL GATE</span>
          <span className="pl-muted">awaiting human approval</span>
        </div>
        {d.proposed_fix_title && (
          <div className="pl-fix"><span className="pl-sublabel">Proposed: </span>{d.proposed_fix_title as string}</div>
        )}
      </div>
    )
  }

  if (ev.type === 'approved') {
    return (
      <div className="pl-row pl-row--approved">
        <span className="pl-ts">{ts}</span>
        <span className="pl-badge pl-badge--ok">✓ APPROVED</span>
        <span className="pl-muted">resuming solver…</span>
      </div>
    )
  }

  if (ev.type === 'rejected') {
    return (
      <div className="pl-row pl-row--failed">
        <span className="pl-ts">{ts}</span>
        <span className="pl-badge pl-badge--fail">✕ REJECTED</span>
      </div>
    )
  }

  if (ev.type === 'expired') {
    return (
      <div className="pl-row pl-row--failed">
        <span className="pl-ts">{ts}</span>
        <span className="pl-badge pl-badge--fail">EXPIRED</span>
        <span className="pl-muted">approval window elapsed</span>
      </div>
    )
  }

  if (ev.type === 'solver_done') {
    return (
      <div className="pl-row pl-row--done">
        <span className="pl-ts">{ts}</span>
        <span className="pl-badge pl-badge--ok">✓ DONE</span>
        {d.outcome && <span className="pl-meta">{d.outcome as string}</span>}
      </div>
    )
  }

  if (ev.type === 'run_failed') {
    return (
      <div className="pl-block pl-block--failed">
        <div className="pl-block__head">
          <span className="pl-ts">{ts}</span>
          <span className="pl-badge pl-badge--fail">RUN FAILED</span>
        </div>
        {d.error && <div className="pl-error">{d.error as string}</div>}
      </div>
    )
  }

  return null
}

export function PipelineLog({ events, correlationId, connected }: PipelineLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  return (
    <section className="panel panel--pipeline">
      <h2 className="panel__title">
        Pipeline Log
        {connected && <span className="live-badge">● LIVE</span>}
        {correlationId && (
          <span className="cid-badge" title={correlationId}>{correlationId.slice(0, 12)}…</span>
        )}
      </h2>
      <div className="pipeline-log">
        {events.length === 0 && (
          <p className="muted">Trigger a scenario to watch the pipeline run in real time.</p>
        )}
        {events.map(ev => <StageBlock key={ev.seq} ev={ev} />)}
        <div ref={bottomRef} />
      </div>
    </section>
  )
}
