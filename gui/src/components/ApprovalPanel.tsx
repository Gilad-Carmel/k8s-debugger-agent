import { useState } from 'react'
import { approveIncident, rejectIncident } from '../services/api'
import type { WorkflowEvent } from '../types/events'

interface ApprovalPanelProps {
  correlationId: string
  approvalEvent: WorkflowEvent
  onDecision: (status: 'approved' | 'rejected') => void
}

export function ApprovalPanel({ correlationId, approvalEvent, onDecision }: ApprovalPanelProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const data = approvalEvent.data as {
    proposed_fix_title?: string
    proposed_fix_description?: string
    deadline_iso?: string
  }

  async function handleDecision(action: 'approve' | 'reject') {
    setBusy(true)
    setError(null)
    try {
      if (action === 'approve') {
        await approveIncident(correlationId, { actor_name: 'gui-user' })
        onDecision('approved')
      } else {
        await rejectIncident(correlationId, { actor_name: 'gui-user' })
        onDecision('rejected')
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Action failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="approval-overlay">
      <div className="approval-modal">
        <div className="approval-modal__header">
          <span className="approval-icon">⚠️</span>
          <h3>Human Approval Required</h3>
        </div>

        <div className="approval-modal__body">
          <div className="approval-cid">
            <strong>Incident:</strong> <code>{correlationId}</code>
          </div>

          {data.proposed_fix_title && (
            <div className="approval-fix">
              <strong>Proposed Fix:</strong>
              <p>{data.proposed_fix_title}</p>
            </div>
          )}

          {data.proposed_fix_description && (
            <div className="approval-desc">
              <p>{data.proposed_fix_description}</p>
            </div>
          )}

          {data.deadline_iso && (
            <div className="approval-deadline">
              ⏱ Expires: {new Date(data.deadline_iso).toLocaleTimeString()}
            </div>
          )}

          {error && <p className="error-msg">{error}</p>}
        </div>

        <div className="approval-modal__actions">
          <button
            className="btn btn--approve"
            disabled={busy}
            onClick={() => handleDecision('approve')}
          >
            {busy ? '…' : '✓ Approve'}
          </button>
          <button
            className="btn btn--reject"
            disabled={busy}
            onClick={() => handleDecision('reject')}
          >
            {busy ? '…' : '✕ Reject'}
          </button>
        </div>
      </div>
    </div>
  )
}
