import { act, render, screen } from '@testing-library/react'
import { MessageDraft, MessageProgress } from './MessageDraft'

afterEach(() => vi.useRealTimers())

const bar = () => screen.getByRole('progressbar', { name: 'Writing the email' })
const step = (label: string) => screen.getByText(label)

describe('Message draft progress', () => {
  it('moves through the steps as time passes and never finishes on its own', () => {
    vi.useFakeTimers()
    render(<MessageProgress />)
    expect(bar()).toHaveAttribute('aria-valuenow', '0')
    expect(step('Choosing the action')).toHaveClass('step-now')
    expect(step('Writing the email')).not.toHaveClass('step-now')

    act(() => vi.advanceTimersByTime(4000))
    expect(bar()).toHaveAttribute('aria-valuenow', '70')
    expect(bar()).toHaveAttribute('aria-valuetext', 'Writing the email, about 70%')
    expect(step('Choosing the action')).toHaveClass('step-done')
    expect(step('Reading the messaging policy')).toHaveClass('step-done')
    expect(step('Writing the email')).toHaveClass('step-now')

    // Only the answer finishes it: a slow request waits at 95%, never 100%.
    act(() => vi.advanceTimersByTime(60_000))
    expect(bar()).toHaveAttribute('aria-valuenow', '95')
    expect(step('Checking it against the rules')).toHaveClass('step-now')
  })

  it('shows the progress while the card is loading', () => {
    render(<MessageDraft state={{ phase: 'loading' }} email="a@example.com" onRegenerate={() => {}} />)
    expect(screen.getByRole('region', { name: 'Message draft' })).toContainElement(bar())
    expect(screen.getByText('This usually takes 4 to 8 seconds.')).toBeInTheDocument()
  })
})
