import { useMemo, useState } from 'react'
import { ApprovalPanel } from './components/ApprovalPanel'
import { PipelineLog } from './components/PipelineLog'
import { PodGrid } from './components/PodGrid'
import { ScenarioButtons } from './components/ScenarioButtons'
import { useEventStream } from './hooks/useEventStream'
import { usePods } from './hooks/usePods'
import type { WorkflowEvent } from './types/events'

export function App() {
  const [correlationId, setCorrelationId] = useState<string | null>(null)

  const { data: podsData, isLoading: podsLoading, error: podsError } = usePods()
  const { events, connected, error: streamError, reset } = useEventStream(correlationId)

  // Derive flags from the event stream
  const awaitingApproval = useMemo(
    () => events.some(e => e.type === 'awaiting_approval') &&
          !events.some(e => e.type === 'approved' || e.type === 'rejected' || e.type === 'expired'),
    [events],
  )

  const approvalEvent = useMemo(
    () => events.find((e): e is WorkflowEvent => e.type === 'awaiting_approval') ?? null,
    [events],
  )

  function handleScenarioTriggered(cid: string) {
    reset()
    setCorrelationId(cid)
  }

  function handleDecision(status: 'approved' | 'rejected') {
    // The approval panel will disappear once the events arrive confirming the decision.
    // No state to set here — the event stream drives the UI.
    void status
  }

  const pods = podsData?.pods ?? []
  const podWarning = podsData?.warning ?? null
  const podError = podsError instanceof Error ? podsError.message : podsError ? String(podsError) : null

  return (
    <>
      <header className="app-header">
        <h1>K8s Debugger</h1>
        <span className="app-header__subtitle">Multi-Agent Workflow Monitor</span>
        {streamError && <span className="error-msg" style={{ marginLeft: 'auto' }}>{streamError}</span>}
      </header>

      <div className="app-layout">
        {/* Left column: pipeline log + pod status */}
        <div className="app-left">
          <PipelineLog events={events} correlationId={correlationId} connected={connected} />
          <PodGrid pods={pods} loading={podsLoading} error={podError} warning={podWarning} />
        </div>

        {/* Right column: triggers */}
        <div className="app-right">
          <ScenarioButtons onTriggered={handleScenarioTriggered} />
        </div>
      </div>

      {/* Approval modal — rendered on top when HITL gate fires */}
      {awaitingApproval && approvalEvent && correlationId && (
        <ApprovalPanel
          correlationId={correlationId}
          approvalEvent={approvalEvent}
          onDecision={handleDecision}
        />
      )}
    </>
  )
}
