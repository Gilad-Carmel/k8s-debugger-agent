import { useCallback, useEffect, useRef, useState } from 'react'
import type { WorkflowEvent } from '../types/events'

interface UseEventStreamResult {
  events: WorkflowEvent[]
  connected: boolean
  error: string | null
  reset: () => void
}

export function useEventStream(correlationId: string | null): UseEventStreamResult {
  const [events, setEvents] = useState<WorkflowEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const lastEventIdRef = useRef<string>('')

  const reset = useCallback(() => {
    setEvents([])
    setConnected(false)
    setError(null)
    lastEventIdRef.current = ''
  }, [])

  useEffect(() => {
    if (!correlationId) return

    // Close any existing connection
    esRef.current?.close()
    setEvents([])
    setError(null)

    const url = new URL('/api/events', window.location.origin)
    url.searchParams.set('correlation_id', correlationId)
    if (lastEventIdRef.current) {
      url.searchParams.set('last_event_id', lastEventIdRef.current)
    }

    const es = new EventSource(url.toString())
    esRef.current = es

    es.onopen = () => setConnected(true)
    es.onerror = () => {
      setConnected(false)
      setError('SSE connection lost — reconnecting…')
    }

    // Listen to all named event types
    const TYPES = [
      'node_started', 'node_completed', 'node_failed',
      'awaiting_approval', 'approved', 'rejected', 'expired',
      'solver_done', 'run_failed', 'reconnect_missed',
    ]

    const TERMINAL = new Set(['solver_done', 'rejected', 'expired', 'run_failed'])

    const handler = (e: MessageEvent) => {
      lastEventIdRef.current = e.lastEventId || ''
      try {
        const event: WorkflowEvent = JSON.parse(e.data as string)
        setEvents(prev => [...prev, event])
        if (TERMINAL.has(event.type)) {
          es.close()
          setConnected(false)
          setError(null)
        }
      } catch {
        // ignore malformed frames
      }
    }

    TYPES.forEach(t => es.addEventListener(t, handler))

    return () => {
      TYPES.forEach(t => es.removeEventListener(t, handler))
      es.close()
      setConnected(false)
    }
  }, [correlationId])

  return { events, connected, error, reset }
}
