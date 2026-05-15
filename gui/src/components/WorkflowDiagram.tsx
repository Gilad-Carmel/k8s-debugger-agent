import { useCallback, useMemo } from 'react'
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Node,
  type Edge,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { GraphNodeName, NodeState, WorkflowEvent } from '../types/events'

interface WorkflowDiagramProps {
  events: WorkflowEvent[]
  awaitingApproval: boolean
}

// ── Fixed positions for the DAG ───────────────────────────────────────────────
const NODE_POSITIONS: Record<string, { x: number; y: number }> = {
  ingest:               { x: 300, y:  20 },
  router:               { x: 300, y: 120 },
  application_expert:   { x:  60, y: 220 },
  network_expert:       { x: 300, y: 220 },
  database_expert:      { x: 540, y: 220 },
  reporter:             { x: 300, y: 320 },
  hitl:                 { x: 300, y: 420 },
  solver:               { x: 300, y: 520 },
}

const NODE_LABELS: Record<string, string> = {
  ingest:             'Ingest',
  router:             'Router',
  application_expert: 'App Expert',
  network_expert:     'Net Expert',
  database_expert:    'DB Expert',
  reporter:           'Reporter',
  hitl:               'HITL Gate',
  solver:             'Solver',
}

const STATUS_COLORS: Record<string, string> = {
  idle:       '#374151',
  active:     '#1e88e5',
  completed:  '#43a047',
  failed:     '#e53935',
  awaiting:   '#fb8c00',
}

// ── Custom node component ─────────────────────────────────────────────────────
function WorkflowNode({ data }: NodeProps) {
  const { label, status } = data as { label: string; status: string }
  const color = STATUS_COLORS[status] ?? STATUS_COLORS.idle
  return (
    <div
      style={{
        padding: '10px 18px',
        borderRadius: 8,
        border: `2px solid ${color}`,
        background: status === 'active' ? `${color}22` : '#1f2937',
        color: '#f9fafb',
        fontSize: 13,
        fontWeight: 600,
        minWidth: 110,
        textAlign: 'center',
        boxShadow: status === 'active' ? `0 0 12px ${color}88` : 'none',
        transition: 'all 0.3s ease',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: color }} />
      <div>{label}</div>
      <div style={{ fontSize: 10, fontWeight: 400, color, marginTop: 3 }}>
        {status === 'idle' ? '' : status.toUpperCase()}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: color }} />
    </div>
  )
}

const nodeTypes = { workflow: WorkflowNode }

// ── Derive node states from the event stream ──────────────────────────────────
function deriveNodeStates(events: WorkflowEvent[], awaitingApproval: boolean): Record<string, NodeState> {
  const states: Record<string, NodeState> = {}

  for (const ev of events) {
    if (!ev.node) continue
    const node = ev.node as GraphNodeName

    if (ev.type === 'node_started') {
      states[node] = { status: 'active' }
    } else if (ev.type === 'node_completed') {
      states[node] = { status: 'completed', data: ev.data }
    } else if (ev.type === 'node_failed') {
      states[node] = { status: 'failed', data: ev.data }
    }
  }

  if (awaitingApproval) {
    states['hitl'] = { status: 'awaiting' }
  } else {
    const approved = events.some(e => e.type === 'approved')
    const rejected = events.some(e => e.type === 'rejected')
    const expired  = events.some(e => e.type === 'expired')
    if (approved) states['hitl'] = { status: 'completed' }
    if (rejected || expired) states['hitl'] = { status: 'failed' }
  }

  return states
}

// ── Main component ────────────────────────────────────────────────────────────
export function WorkflowDiagram({ events, awaitingApproval }: WorkflowDiagramProps) {
  const nodeStates = useMemo(
    () => deriveNodeStates(events, awaitingApproval),
    [events, awaitingApproval],
  )

  const nodes: Node[] = useMemo(
    () =>
      Object.entries(NODE_POSITIONS).map(([id, position]) => ({
        id,
        type: 'workflow',
        position,
        data: {
          label: NODE_LABELS[id] ?? id,
          status: nodeStates[id]?.status ?? 'idle',
        },
      })),
    [nodeStates],
  )

  const edges: Edge[] = useMemo(
    () => [
      { id: 'ingest-router',   source: 'ingest',   target: 'router',             animated: true },
      { id: 'router-app',      source: 'router',   target: 'application_expert', animated: true },
      { id: 'router-net',      source: 'router',   target: 'network_expert',     animated: true },
      { id: 'router-db',       source: 'router',   target: 'database_expert',    animated: true },
      { id: 'app-reporter',    source: 'application_expert', target: 'reporter', animated: true },
      { id: 'net-reporter',    source: 'network_expert',     target: 'reporter', animated: true },
      { id: 'db-reporter',     source: 'database_expert',    target: 'reporter', animated: true },
      { id: 'reporter-hitl',   source: 'reporter', target: 'hitl',    animated: true },
      { id: 'hitl-solver',     source: 'hitl',     target: 'solver',  animated: true },
    ],
    [],
  )

  const onInit = useCallback(() => {}, [])

  return (
    <section className="panel panel--diagram">
      <h2 className="panel__title">Workflow Visualizer</h2>
      <div style={{ flex: 1, minHeight: 0 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onInit={onInit}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#374151" gap={20} />
          <Controls />
        </ReactFlow>
      </div>
    </section>
  )
}
