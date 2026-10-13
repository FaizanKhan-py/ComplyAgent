import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, setUnauthorizedHandler, tokenStore } from './api/client'

type AuthState =
  | { phase: 'checking' }
  | { phase: 'unreachable'; error: Error }
  | { phase: 'signed-out'; expired: boolean }
  | { phase: 'signed-in'; authRequired: boolean }

interface AuthContextValue {
  state: AuthState
  signIn: (token: string) => Promise<void>
  signOut: () => void
  retry: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ phase: 'checking' })

  // State is only set from promise callbacks, so this is safe to start from an effect.
  const check = useCallback(
    () =>
      api
        .authStatus()
        .then(({ auth_required }): Promise<AuthState> | AuthState => {
          if (!auth_required) return { phase: 'signed-in', authRequired: false }
          const stored = tokenStore.get()
          if (!stored) return { phase: 'signed-out', expired: false }
          return api.verifyToken(stored).then(
            (): AuthState => ({ phase: 'signed-in', authRequired: true }),
            (): AuthState => {
              tokenStore.clear()
              return { phase: 'signed-out', expired: true }
            },
          )
        })
        .then(setState, (error: unknown) =>
          setState({
            phase: 'unreachable',
            error: error instanceof Error ? error : new Error('Could not reach the API'),
          }),
        ),
    [],
  )

  useEffect(() => {
    void check()
    setUnauthorizedHandler(() => {
      tokenStore.clear()
      setState({ phase: 'signed-out', expired: true })
    })
    return () => setUnauthorizedHandler(null)
  }, [check])

  const signIn = useCallback(async (token: string) => {
    await api.verifyToken(token)
    tokenStore.set(token)
    setState({ phase: 'signed-in', authRequired: true })
  }, [])

  const signOut = useCallback(() => {
    tokenStore.clear()
    setState({ phase: 'signed-out', expired: false })
  }, [])

  const retry = useCallback(() => {
    setState({ phase: 'checking' })
    void check()
  }, [check])

  return (
    <AuthContext.Provider value={{ state, signIn, signOut, retry }}>
      {children}
    </AuthContext.Provider>
  )
}

// eslint-disable-next-line react/only-export-components
export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
