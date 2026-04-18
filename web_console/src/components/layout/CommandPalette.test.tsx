import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { CommandPalette } from './CommandPalette';

describe('CommandPalette', () => {
  beforeEach(() => {
    localStorage.clear();
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/gui/commands')) {
        return new Response(JSON.stringify({
          ok: true,
          commands: [
            { name: 'help', description: 'Show available commands', aliases: [] },
            { name: 'model', description: 'Switch model for this session', aliases: [] },
          ],
        }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as typeof fetch;
  });

  it('prefills non-direct slash commands when selected', async () => {
    const prefill = vi.fn();
    window.addEventListener('hermes:prefillComposer', prefill as EventListener);

    render(<CommandPalette open onClose={() => {}} />);

    fireEvent.change(screen.getByPlaceholderText(/Search routes, actions, or commands/i), {
      target: { value: 'model' },
    });

    const action = await screen.findByRole('button', { name: /Run \/model/i });
    fireEvent.click(action);

    await waitFor(() => {
      expect(prefill).toHaveBeenCalled();
    });

    window.removeEventListener('hermes:prefillComposer', prefill as EventListener);
  });

  it('shows pinned actions in the pinned section after pinning', async () => {
    const { rerender } = render(<CommandPalette open onClose={() => {}} />);

    const action = await screen.findByRole('button', { name: /Open Usage/i });
    expect(action).toBeInTheDocument();
    const pin = within(action).getByRole('button', { name: /☆ pin/i });
    fireEvent.click(pin);

    rerender(<CommandPalette open={false} onClose={() => {}} />);
    rerender(<CommandPalette open onClose={() => {}} />);

    const pinnedHeadings = await screen.findAllByText('Pinned');
    expect(pinnedHeadings.length).toBeGreaterThan(0);
    const pinnedSection = pinnedHeadings[0].parentElement as HTMLElement;
    expect(within(pinnedSection).getByRole('button', { name: /Open Usage/i })).toBeInTheDocument();
  });
});
