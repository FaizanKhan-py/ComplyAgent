import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answer, config, mockApi } from '../test/fixtures'
import { ChatWidget } from './ChatWidget'

afterEach(() => vi.unstubAllGlobals())

const chat = () =>
  mockApi({
    'GET /api/admin/config': () => ({ body: config }),
    'POST /api/assistant/chat': () => ({
      body: { ...answer({ answer: 'Up to six months [1].' }), conversation_id: 'c1', steps: [] },
    }),
  })

const launcher = (name: RegExp) => screen.getByRole('button', { name })

describe('AI Assistant pop-up', () => {
  it('opens and closes from the button, and keeps the conversation while closed', async () => {
    const user = userEvent.setup()
    const api = chat()
    render(<ChatWidget isAdmin={false} hidden={false} />)

    // Nothing is loaded until the first open.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(launcher(/AI Assistant/)).toHaveAttribute('aria-expanded', 'false')
    expect(api.calls).toHaveLength(0)

    await user.click(launcher(/AI Assistant/))
    const panel = screen.getByRole('dialog', { name: 'AI Assistant' })
    expect(launcher(/Close$/)).toHaveAttribute('aria-expanded', 'true')
    await user.type(screen.getByLabelText('Question'), 'How long can a hardship plan last?{Enter}')
    expect(await screen.findByText(/Up to six months/)).toBeInTheDocument()

    await user.click(launcher(/Close$/))
    expect(panel).not.toBeVisible()
    await user.click(launcher(/AI Assistant/))
    expect(screen.getByRole('dialog', { name: 'AI Assistant' })).toBeVisible()
    expect(screen.getByText(/Up to six months/)).toBeInTheDocument() // same conversation
    expect(api.count('POST /api/assistant/chat')).toBe(1)
  })

  it('closes with the x button and with Escape', async () => {
    const user = userEvent.setup()
    chat()
    render(<ChatWidget isAdmin={false} hidden={false} />)

    await user.click(launcher(/AI Assistant/))
    await user.click(screen.getByRole('button', { name: 'Close the AI Assistant' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(launcher(/AI Assistant/)).toHaveFocus()

    await user.click(launcher(/AI Assistant/))
    expect(screen.getByRole('dialog')).toBeVisible()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(launcher(/AI Assistant/)).toHaveFocus()
  })

  it('stays out of the way on the full-page chat', () => {
    chat()
    const { container } = render(<ChatWidget isAdmin hidden />)
    expect(container.querySelector('.chat-widget')).not.toBeVisible()
  })
})
