import axios from 'axios'
import type {
  ApprovalResponse,
  DemoScenario,
  GuiApprovalRequest,
  PodsResponse,
  ScenarioTriggerResponse,
} from '../types/events'

const http = axios.create({ baseURL: '/api' })

export async function fetchPods(namespace = 'demo'): Promise<PodsResponse> {
  const { data } = await http.get<PodsResponse>('/pods', { params: { namespace } })
  return data
}

export async function triggerScenario(scenario: DemoScenario): Promise<ScenarioTriggerResponse> {
  const { data } = await http.post<ScenarioTriggerResponse>(`/demo/trigger/${scenario}`)
  return data
}

export async function approveIncident(
  correlationId: string,
  body: GuiApprovalRequest = {},
): Promise<ApprovalResponse> {
  const { data } = await http.post<ApprovalResponse>(
    `/approval/${correlationId}/approve`,
    body,
  )
  return data
}

export async function rejectIncident(
  correlationId: string,
  body: GuiApprovalRequest = {},
): Promise<ApprovalResponse> {
  const { data } = await http.post<ApprovalResponse>(
    `/approval/${correlationId}/reject`,
    body,
  )
  return data
}
