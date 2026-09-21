import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MessageBubble } from '@/components/chat/message-bubble';

describe('MessageBubble errors', () => {
  it('offers a sign-in link when the token was refused (401)', () => {
    render(<MessageBubble role="agent" text="Bearer token is no longer active." errorStatus={401} />);
    expect(screen.getByText('Bearer token is no longer active.', { exact: false })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Sign in again' }).getAttribute('href')).toBe('/api/auth/login');
  });

  it('does not for other errors', () => {
    render(<MessageBubble role="agent" text="This content was blocked." errorStatus={500} />);
    expect(screen.queryByRole('link', { name: 'Sign in again' })).toBeNull();
  });
});
