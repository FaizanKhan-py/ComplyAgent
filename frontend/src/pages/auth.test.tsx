import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'
import { api } from '../api/client'
import type { User } from '../api/types'
import { AuthProvider } from '../auth'
import { passwordStrength } from '../lib/validation'
import { config, health, mockApi, stats } from '../test/fixtures'

afterEach(async () => {
  // Drop the in-memory CSRF token so one test cannot leak a session into the next.
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"ok":true}')))
  await api.logout()
  vi.unstubAllGlobals()
})

const admin: User = {
  id: 1,
  email: 'root@example.com',
  full_name: 'Root Admin',
  role: 'admin',
  created_at: '2026-10-13T09:00:00+00:00',
  last_login_at: '2026-10-13T09:30:00+00:00',
}
const member: User = { ...admin, id: 2, email: 'ada@example.com', full_name: 'Ada Lovelace', role: 'user' }

const signedOut = () => ({
  status: 401,
  body: { error: { code: 'unauthorized', message: 'Sign in to continue.' } },
})
const session = (user: User) => () => ({ body: { user, csrf_token: 'csrf-123' } })
const dashboardRoutes = {
  'GET /api/admin/kb/stats': () => ({ body: stats() }),
  'GET /api/admin/activity?limit=8': () => ({ body: { events: [] } }),
  'GET /api/admin/health': () => ({ body: health }),
}

const app = (path = '/') =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  )
const headersOf = (init: RequestInit) => init.headers as Record<string, string>

describe('Sign in', () => {
  it('sends signed-out visitors to the login page without calling any private endpoint', async () => {
    const calls = mockApi({ 'GET /api/auth/me': signedOut })
    app('/documents')
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(calls.calls.map((c) => c.key)).toEqual(['GET /api/auth/me'])
    expect(screen.getByRole('link', { name: 'Create an account' })).toHaveAttribute('href', '/signup')
    expect(screen.queryByText('Your session ended. Sign in again.')).not.toBeInTheDocument()
  })

  it('validates the form, shows the server error, then signs in and out for real', async () => {
    const user = userEvent.setup()
    let signedIn = false
    const calls = mockApi({
      'GET /api/auth/me': () => (signedIn ? session(admin)() : signedOut()),
      'POST /api/auth/login': (init) => {
        const body = JSON.parse(init.body as string)
        if (body.password !== 'correct horse battery') {
          return { status: 401, body: { error: { code: 'invalid_credentials', message: 'Incorrect email or password.' } } }
        }
        signedIn = true
        return session(admin)()
      },
      'POST /api/auth/logout': () => {
        signedIn = false
        return { body: { ok: true } }
      },
      ...dashboardRoutes,
    })
    app('/')

    const email = await screen.findByLabelText('Email address')
    const password = screen.getByLabelText('Password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(screen.getByText('Enter your email address.')).toBeInTheDocument()
    expect(screen.getByText('Enter your password.')).toBeInTheDocument()
    await user.type(email, 'not-an-email')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(screen.getByText('Enter a valid email address.')).toBeInTheDocument()
    expect(calls.count('POST /api/auth/login')).toBe(0) // nothing was sent yet

    await user.clear(email)
    await user.type(email, '  root@example.com ')
    await user.type(password, 'wrong{Enter}')
    expect(await screen.findByText('Incorrect email or password.')).toBeInTheDocument()
    expect(password).toHaveValue('')

    await user.type(password, 'correct horse battery{Enter}')
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    const login = calls.calls.filter((c) => c.key === 'POST /api/auth/login').at(-1)!
    expect(JSON.parse(login.init.body as string)).toEqual({
      email: 'root@example.com',
      password: 'correct horse battery',
    })
    expect(screen.getByText('Root Admin')).toBeInTheDocument()
    expect(screen.getByText('root@example.com')).toBeInTheDocument()
    expect(screen.getByText('Administrator')).toBeInTheDocument()
    for (const link of ['Dashboard', 'Documents', 'Knowledge Base', 'RAG Playground']) {
      expect(screen.getByRole('link', { name: link })).toBeInTheDocument()
    }
    // Nothing about the session is kept where scripts or other tabs could read it.
    expect(sessionStorage.length + localStorage.length).toBe(0)

    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    const logout = calls.calls.find((c) => c.key === 'POST /api/auth/logout')!
    expect(headersOf(logout.init)['X-CSRF-Token']).toBe('csrf-123')
  })

  it('shows and hides the password', async () => {
    const user = userEvent.setup()
    mockApi({ 'GET /api/auth/me': signedOut })
    app('/login')
    const password = await screen.findByLabelText('Password')
    expect(password).toHaveAttribute('type', 'password')
    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(password).toHaveAttribute('type', 'text')
    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(password).toHaveAttribute('type', 'password')

    await user.click(screen.getByRole('button', { name: 'Forgot your password?' }))
    expect(screen.getByText(/Ask a ComplyAgent administrator/)).toBeInTheDocument()
  })

  it('restores the session after a reload and returns to the page that was open', async () => {
    mockApi({
      'GET /api/auth/me': session(admin),
      'GET /api/admin/documents': () => ({ body: { documents: [] } }),
    })
    app('/documents')
    expect(await screen.findByRole('heading', { name: 'Documents' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  })

  it('returns to the login page when the server says the session has ended', async () => {
    mockApi({ 'GET /api/auth/me': session(admin), 'GET /api/admin/documents': signedOut })
    app('/documents')
    expect(await screen.findByText('Your session ended. Sign in again.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('reports an unreachable API instead of showing the login form', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    app('/')
    expect(await screen.findByText(/Cannot reach the ComplyAgent API/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  })
})

describe('Roles', () => {
  it('gives an ordinary user only the playground, and never calls admin endpoints', async () => {
    const calls = mockApi({
      'GET /api/auth/me': session(member),
      'GET /api/admin/config': () => ({ body: { ...config, llm_model: null, llm_provider: null } }),
    })
    app('/documents') // an admin page: the user is moved to the playground
    expect(await screen.findByRole('heading', { name: 'RAG Playground' })).toBeInTheDocument()
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('Member')).toBeInTheDocument()
    expect(screen.getAllByRole('link').map((a) => a.textContent)).toEqual([
      'Skip to content',
      'RAG Playground',
    ])
    expect(await screen.findByText(/An administrator can enable generated answers/)).toBeInTheDocument()
    await waitFor(() => expect(calls.count('GET /api/admin/config')).toBe(1))
    expect(calls.calls.map((c) => c.key).sort()).toEqual(['GET /api/admin/config', 'GET /api/auth/me'])
  })
})

describe('Sign up', () => {
  const fill = async (user: ReturnType<typeof userEvent.setup>, values: Record<string, string>) => {
    for (const [label, value] of Object.entries(values)) {
      const field = screen.getByLabelText(label)
      await user.clear(field)
      if (value) await user.type(field, value)
    }
  }

  it('validates every field before sending anything', async () => {
    const user = userEvent.setup()
    const calls = mockApi({ 'GET /api/auth/me': signedOut })
    app('/signup')
    await screen.findByRole('heading', { name: 'Create your account' })

    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(screen.getByText('Enter your full name.')).toBeInTheDocument()
    expect(screen.getByText('Enter your email address.')).toBeInTheDocument()
    expect(screen.getByText('Enter a password.')).toBeInTheDocument()
    expect(screen.getByText('Enter the password again.')).toBeInTheDocument()

    await fill(user, {
      'Full name': 'Ada Lovelace',
      'Email address': 'ada@example',
      Password: 'short',
      'Confirm password': 'different',
    })
    expect(screen.getByText('Password strength: Too short')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(screen.getByText('Enter a valid email address.')).toBeInTheDocument()
    expect(screen.getByText('Use at least 8 characters.')).toBeInTheDocument()
    expect(screen.getByText('The passwords do not match.')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toHaveAttribute('aria-invalid', 'true')
    expect(calls.count('POST /api/auth/register')).toBe(0)
  })

  it('shows a duplicate email next to the field, then registers and hands over to sign-in', async () => {
    const user = userEvent.setup()
    const calls = mockApi({
      'GET /api/auth/me': signedOut,
      'POST /api/auth/register': (init) => {
        const body = JSON.parse(init.body as string)
        return body.email === 'taken@example.com'
          ? { status: 409, body: { error: { code: 'email_taken', message: 'An account with this email address already exists.' } } }
          : { status: 201, body: { ...member, email: body.email } }
      },
    })
    app('/signup')
    await screen.findByRole('heading', { name: 'Create your account' })
    await fill(user, {
      'Full name': ' Ada Lovelace ',
      'Email address': 'taken@example.com',
      Password: 'correct horse battery',
      'Confirm password': 'correct horse battery',
    })
    expect(screen.getByText('Password strength: Good')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByText('An account with this email address already exists.')).toBeInTheDocument()
    expect(screen.getByLabelText('Email address')).toHaveAttribute('aria-invalid', 'true')

    await fill(user, { 'Email address': 'ada@example.com' })
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByText('Your account was created. Sign in to continue.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByLabelText('Email address')).toHaveValue('ada@example.com')

    const sent = JSON.parse(calls.calls.at(-1)!.init.body as string)
    // Only these three fields are sent: the page has no way to ask for a role.
    expect(sent).toEqual({
      full_name: 'Ada Lovelace',
      email: 'ada@example.com',
      password: 'correct horse battery',
    })
    expect(calls.count('POST /api/auth/login')).toBe(0)
  })

  it('rates password strength by length and variety', () => {
    expect(passwordStrength('short').label).toBe('Too short')
    expect(passwordStrength('password').label).toBe('Weak')
    expect(passwordStrength('aaaaaaaaaaaaaaaaaaaa').label).toBe('Weak')
    expect(passwordStrength('longerpassword').label).toBe('Fair')
    expect(passwordStrength('Longer-passw0rd').label).toBe('Good')
    expect(passwordStrength('Correct-horse-battery-9').label).toBe('Strong')
  })
})
