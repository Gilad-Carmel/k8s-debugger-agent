import { useQuery } from '@tanstack/react-query'
import { fetchPods } from '../services/api'
import type { PodsResponse } from '../types/events'

export function usePods(namespace = 'demo', refetchInterval = 3000) {
  return useQuery<PodsResponse>({
    queryKey: ['pods', namespace],
    queryFn: () => fetchPods(namespace),
    refetchInterval,
    retry: 2,
  })
}
