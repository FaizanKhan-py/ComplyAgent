import { mockApi } from '../test/fixtures'
import { api, ApiError, setUnauthorizedHandler } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedHandler(null)
})

describe('api client', () => {
  it('sends the session cookie but never a token it stored itself', async () => {
    const { calls } = mockApi({ 'GET /api/admin/documents': () => ({ body: { documents: [] } }) })
    await expect(api.listDocuments()).resolves.toEqual([])
    expect(calls[0].init.headers).toEqual({})
    expect(calls[0].init.credentials).toBe('same-origin')
    expect(sessionStorage.length + localStorage.length).toBe(0)
  })

  it('adds the CSRF token from sign-in to state-changing requests only, until sign-out', async () => {
    const user = { id: 1, email: 'ada@example.com', full_name: 'Ada', role: 'user' }
    const { calls } = mockApi({
      'POST /api/auth/login': () => ({ body: { user, csrf_token: 'csrf-abc' } }),
      'GET /api/admin/kb/stats': () => ({ body: {} }),
      'POST /api/admin/kb/rebuild': () => ({ status: 202, body: { queued: 0 } }),
      'POST /api/auth/logout': () => ({ body: { ok: true } }),
    })
    await expect(api.login('ada@example.com', 'a password')).resolves.toEqual(user)
    expect(calls[0].init.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(JSON.parse(calls[0].init.body as string)).toEqual({
      email: 'ada@example.com',
      password: 'a password',
    })

    await api.stats()
    await api.rebuild()
    await api.logout()
    await api.rebuild()
    expect(calls[1].init.headers).toEqual({}) // a read
    expect(calls[2].init.headers).toEqual({ 'X-CSRF-Token': 'csrf-abc' })
    expect(calls[3].init.headers).toEqual({ 'X-CSRF-Token': 'csrf-abc' }) // the logout itself
    expect(calls[4].init.headers).toEqual({}) // forgotten after sign-out
  })

  it('uploads the file as multipart form data under the "file" field', async () => {
    const { calls } = mockApi({ 'POST /api/admin/documents': () => ({ status: 202, body: {} }) })
    const file = new File(['policy text'], 'policy.txt', { type: 'text/plain' })
    await api.uploadDocument(file)
    const body = calls[0].init.body as FormData
    expect(body).toBeInstanceOf(FormData)
    expect((body.get('file') as File).name).toBe('policy.txt')
    // The browser must set the multipart boundary itself.
    expect(calls[0].init.headers).toEqual({})
  })

  it('posts queries as JSON', async () => {
    const { calls } = mockApi({ 'POST /api/admin/rag/query': () => ({ body: {} }) })
    await api.query({ question: 'What is the fee?', top_k: 3 })
    expect(calls[0].init.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ question: 'What is the fee?', top_k: 3 })
  })

  it('uses the right method and path for delete, reindex and rebuild', async () => {
    const { calls } = mockApi({
      'DELETE /api/admin/documents/abc': () => ({ body: { id: 'abc', deleted: true, chunks_removed: 2 } }),
      'POST /api/admin/documents/abc/reindex': () => ({ status: 202, body: {} }),
      'POST /api/admin/kb/rebuild': () => ({ status: 202, body: { queued: 1 } }),
    })
    await api.deleteDocument('abc')
    await api.reindexDocument('abc')
    await api.rebuild()
    expect(calls).toHaveLength(3)
  })

  it('turns the backend error envelope into an ApiError', async () => {
    mockApi({
      'POST /api/admin/documents': () => ({
        status: 409,
        body: { error: { code: 'duplicate_document', message: 'This file was already uploaded.' } },
      }),
    })
    const error = await api.uploadDocument(new File(['x'], 'a.txt')).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 409,
      code: 'duplicate_document',
      message: 'This file was already uploaded.',
    })
  })

  it('reports an unreachable backend clearly', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(api.stats()).rejects.toMatchObject({ status: 0, code: 'network_error' })

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('proxy error', { status: 502 })))
    await expect(api.stats()).rejects.toMatchObject({ status: 502, code: 'network_error' })
  })

  it('reports a lost session to the app, but not a failed sign-in', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    const unauthorized = () => ({
      status: 401,
      body: { error: { code: 'unauthorized', message: 'Sign in to continue.' } },
    })
    mockApi({
      'GET /api/admin/kb/stats': unauthorized,
      'GET /api/auth/me': unauthorized,
      'POST /api/auth/login': unauthorized,
    })

    await expect(api.me()).rejects.toMatchObject({ status: 401 })
    await expect(api.login('ada@example.com', 'wrong')).rejects.toMatchObject({ status: 401 })
    expect(handler).not.toHaveBeenCalled()
    await expect(api.stats()).rejects.toMatchObject({ status: 401 })
    expect(handler).toHaveBeenCalledOnce()
  })
})
