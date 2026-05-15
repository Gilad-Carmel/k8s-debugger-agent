export type WorkflowEventType =
  | 'node_started'
  | 'node_completed'
  | 'node_failed'
  | 'awaiting_approval'
  | 'approved'
  | 'rejected'
  | 'expired'
  | 'solver_done'
  | 'run_failed'
  | 'reconnect_missed'

export interface WorkflowEvent {
  seq: number
  correlation_id: string
  type: WorkflowEventType
  node?: string
  ts: string
  data: Record<string, unknown>
}

export type PodPhase = 'Pending' | 'Running' | 'Succeeded' | 'Failed' | 'Unknown'

export interface PodStatus {
  name: string
  namespace: string
  phase: PodPhase
  ready: boolean
  restart_count: number
  message?: string
  ts: string
}

export interface PodsResponse {
  pods: PodStatus[]
  fetched_at: string
}

export type DemoScenario = 'crash' | 'bad-deploy' | 'oom' | 'scale'

export interface ScenarioTriggerResponse {
  correlation_id: string
  scenario: DemoScenario
  started_at: string
}

export interface GuiApprovalRequest {
  actor_name?: string
  reason?: string
}

export interface ApprovalResponse {
  correlation_id: string
  status: 'approved' | 'rejected'
}

export type NodeStatus = 'idle' | 'active' | 'completed' | 'failed' | 'awaiting'

export interface NodeState {
  status: NodeStatus
  data?: Record<string, unknown>
}

export type GraphNodeName =
  | 'ingest'
  | 'router'
  | 'application_expert'
  | 'network_expert'
  | 'database_expert'
  | 'reporter'
  | 'hitl'
  | 'solver'
