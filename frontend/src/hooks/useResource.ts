import { useCallback, useEffect, useRef, useState } from 'react'

export interface Resource<T> {
  data: T | null
  error: Error | null
  /** True until the first response (success or failure) arrives. */
  loading: boolean
  /** True while a later refresh is in flight; `data` stays visible meanwhile. */
  refreshing: boolean
  refresh: () => Promise<void>
}

/**
 * Loads data on mount and exposes a manual refresh. `pollMs` may be a function of the latest
 * data, so a page can poll only while something is still changing (e.g. documents processing).
 */
export function useResource<T>(
  fetcher: () => Promise<T>,
  pollMs?: number | ((data: T) => number | null),
): Resource<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const fetcherRef = useRef(fetcher)
  const alive = useRef(true)

  useEffect(() => {
    fetcherRef.current = fetcher
  })

  // State is only set from the promise callbacks, never synchronously.
  const load = useCallback(
    () =>
      fetcherRef
        .current()
        .then(
          (result) => {
            if (!alive.current) return
            setData(result)
            setError(null)
          },
          (err: unknown) => {
            if (!alive.current) return
            setError(err instanceof Error ? err : new Error('Request failed'))
          },
        )
        .finally(() => {
          if (!alive.current) return
          setLoading(false)
          setRefreshing(false)
        }),
    [],
  )

  useEffect(() => {
    alive.current = true
    void load()
    return () => {
      alive.current = false
    }
  }, [load])

  const interval = data === null ? null : typeof pollMs === 'function' ? pollMs(data) : pollMs
  useEffect(() => {
    if (!interval) return
    const id = window.setInterval(() => void load(), interval)
    return () => window.clearInterval(id)
  }, [interval, load])

  const refresh = useCallback(() => {
    setRefreshing(true)
    return load()
  }, [load])
  return { data, error, loading, refreshing, refresh }
}
