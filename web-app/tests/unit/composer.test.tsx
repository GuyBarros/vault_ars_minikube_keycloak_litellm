import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import React, { useState } from 'react';
import { Composer } from '@/components/chat/composer';

function Harness() {
  const [v, setV] = useState('');
  return (
    <Composer
      value={v}
      onChange={setV}
      onSend={vi.fn()}
      onClear={() => setV('')}
      disabled={false}
    />
  );
}

describe('Composer', () => {
  it('updates the controlled value when typing and reflects it in the counter', () => {
    render(<Harness />);
    const ta = screen.getByLabelText('Message your AI agent') as HTMLTextAreaElement;
    expect(ta.value).toBe('');
    fireEvent.change(ta, { target: { value: 'hello' } });
    expect(ta.value).toBe('hello');
    expect(screen.getByText('5/1000')).toBeTruthy();
  });

  it('enables Send only when text is present', () => {
    render(<Harness />);
    const send = screen.getByText('Send').closest('button') as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    const ta = screen.getByLabelText('Message your AI agent');
    fireEvent.change(ta, { target: { value: 'hi' } });
    expect(send.disabled).toBe(false);
  });

  it('Clear conversation always enabled and clears input', () => {
    render(<Harness />);
    const ta = screen.getByLabelText('Message your AI agent') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'something' } });
    expect(ta.value).toBe('something');
    const clear = screen.getByText('Clear conversation').closest('button') as HTMLButtonElement;
    expect(clear.disabled).toBe(false);
    fireEvent.click(clear);
    expect(ta.value).toBe('');
    expect(screen.getByText('0/1000')).toBeTruthy();
  });
});
