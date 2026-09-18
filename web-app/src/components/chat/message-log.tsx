'use client';

import { useEffect, useRef } from 'react';
import { MessageBubble, type MessageRole } from '@/components/chat/message-bubble';

export interface DisplayMessage {
  role: MessageRole;
  text: string;
  errorStatus?: number;
}

interface Props {
  messages: DisplayMessage[];
  pending?: { role: 'agent'; text: string; isTyping: boolean } | null;
}

export function MessageLog({ messages, pending }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [messages, pending]);

  return (
    <section className="chat" aria-label="Conversation">
      <div className="chat__head">
        <div className="eyebrow">Session</div>
        <div className="chat__title">Your helpful AI assistant</div>
      </div>
      <div className="chat__log" ref={ref} aria-live="polite">
        {messages.map((m, i) => (
          <MessageBubble key={i} role={m.role} text={m.text} errorStatus={m.errorStatus} />
        ))}
        {pending ? (
          <MessageBubble
            role="agent"
            text={pending.text}
            showTyping={pending.isTyping && !pending.text}
          />
        ) : null}
      </div>
    </section>
  );
}
