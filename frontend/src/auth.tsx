import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, ApiError, setUnauthorizedHandler } from './api/client'
import type { RegisterRequest, User } from './api/types'

type AuthState =
  | { phase: 'checking' }
  | { phase: 'unreachable'; error: Error }
  | { phase: 'signed-out'; expired: boolean }
  | { phase: 'signed-in'; user: User }

interface AuthContextValue {
  state: AuthState
  /** The signed-in user, or null. */
  user: User | null
  signIn: (email: string, password: string) => Promise<void>
  /** Creates the account. It does not sign in: the person signs in with their new password. */
  signUp: (body: RegisterRequest) => Promise<User>
  signOut: () => Promise<void>
  retry: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ phase: 'checking' })

  // The session lives in an HttpOnly cookie, so the only way to know who is signed in (for
  // example after a page reload) is to ask the server.
  const check = useCallback(
    () =>
      api.me().then(
        (user) => setState({ phase: 'signed-in', user }),
        (error: unknown) =>
          setState(
            error instanceof ApiError && error.status === 401
              ? { phase: 'signed-out', expired: false }
              : {
                  phase: 'unreachable',
                  error: error instanceof Error ? error : new Error('Could not reach the API'),
                },
          ),
      ),
    [],
  )

  useEffect(() => {
    void check()
    // Any later 401 means the session expired or was ended on the server.
    setUnauthorizedHandler(() => setState({ phase: 'signed-out', expired: true }))
    return () => setUnauthorizedHandler(null)
  }, [check])

  const signIn = useCallback(async (email: string, password: string) => {
    const user = await api.login(email, password)
    setState({ phase: 'signed-in', user })
  }, [])

  const signUp = useCallback((body: RegisterRequest) => api.register(body), [])

  const signOut = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      /* Offline or already expired: this browser is signed out either way. */
    }
    setState({ phase: 'signed-out', expired: false })
  }, [])

  const retry = useCallback(() => {
    setState({ phase: 'checking' })
    void check()
  }, [check])

  const user = state.phase === 'signed-in' ? state.user : null
  return (
    <AuthContext.Provider value={{ state, user, signIn, signUp, signOut, retry }}>
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
