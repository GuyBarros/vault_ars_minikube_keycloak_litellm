'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ActionableNotification, Tag } from '@carbon/react';
import { HeroPanel } from '@/components/chat/hero-panel';
import { MessageLog, type DisplayMessage } from '@/components/chat/message-log';
import { Composer } from '@/components/chat/composer';
import { TokenInspector } from '@/components/inspector/token-inspector';
import { AgentRequestError, streamAgent } from '@/components/chat/stream-client';
import { STEP_UP_CHANNEL, type StepUpResult } from '@/lib/auth/step-up';
import type { LoaState } from '@/lib/auth/loa';
import type { ChatMessage } from '@/types/agent';

interface Props {
  username: string;
}

export function ChatWorkspace({ username }: Props) {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [pending, setPending] = useState<{ role: 'agent'; text: string; isTyping: boolean } | null>(
    null,
  );
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [agentTokensRefreshKey, setAgentTokensRefreshKey] = useState(0);
  // Set when the agent's last tool call needed LoA 2; `text` is the message to retry afterwards.
  const [stepUp, setStepUp] = useState<{ text: string; verifying: boolean } | null>(null);
  // Level-2 window: when it lapses (epoch seconds) and how long a window is. Enforced by the
  // PEP; the UI only mirrors it. `now` ticks once a second while a step-up is active.
  const [elevatedUntil, setElevatedUntil] = useState<number | null>(null);
  const [ttlSeconds, setTtlSeconds] = useState(300);
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const focusComposer = useCallback(() => {
    composerRef.current?.focus();
  }, []);

  // `retry` re-sends the message the step-up interrupted: its bubble and the agent's
  // "step-up required" reply stay on screen but are left out of the history.
  const sendText = useCallback(async (text: string, retry = false) => {
    if (!text || sending) return;

    const history: ChatMessage[] = (retry ? messages.slice(0, -2) : messages).map((m) => ({
      role: m.role === 'user' ? 'user' : 'assistant',
      content: m.text,
    }));

    if (!retry) setMessages((prev) => [...prev, { role: 'user', text }]);
    setInput('');
    setPending({ role: 'agent', text: '', isTyping: true });
    setSending(true);

    const ac = new AbortController();
    abortRef.current = ac;

    // The agent completes its tool-calling round (OBO/actor token exchange
    // included) before it starts streaming any answer text, so the tokens
    // are already current by the time the first chunk arrives — refresh
    // then rather than waiting for the whole response to finish streaming.
    // Fall back to refreshing on completion/error in case no chunk ever
    // arrives (empty response, or a failure before any text streamed).
    let acc = '';
    let tokensRefreshed = false;
    const refreshTokensOnce = () => {
      if (tokensRefreshed) return;
      tokensRefreshed = true;
      setAgentTokensRefreshKey((n) => n + 1);
    };

    await streamAgent(text, history, ac.signal, {
      onChunk: (chunk) => {
        acc += chunk;
        setPending({ role: 'agent', text: acc, isTyping: false });
        refreshTokensOnce();
      },
      onDone: () => {
        setMessages((prev) => [...prev, { role: 'agent', text: acc || '(empty response)' }]);
        setPending(null);
        setSending(false);
        refreshTokensOnce();
        void checkStepUp(text);
      },
      onError: (err) => {
        const status = err instanceof AgentRequestError ? err.status : undefined;
        setMessages((prev) => {
          const next: DisplayMessage[] = [...prev];
          if (acc) next.push({ role: 'agent', text: acc });
          next.push({ role: 'agent', text: err.message, errorStatus: status });
          return next;
        });
        setPending(null);
        setSending(false);
        refreshTokensOnce();
      },
    });
  }, [messages, sending]);

  const handleSend = useCallback(() => sendText(input.trim()), [input, sendText]);

  const refreshLoa = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/me', { cache: 'no-store' });
      if (!res.ok) return;
      const { loa } = (await res.json()) as { loa?: LoaState };
      if (!loa) return;
      setElevatedUntil(loa.elevatedUntil);
      setTtlSeconds(loa.ttlSeconds);
    } catch {
      // Leave the current state; the PEP still enforces the window.
    }
  }, []);

  useEffect(() => {
    void refreshLoa();
  }, [refreshLoa]);

  useEffect(() => {
    if (elevatedUntil === null) return;
    setNow(Math.floor(Date.now() / 1000));
    const timer = window.setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [elevatedUntil]);

  const remaining = elevatedUntil === null ? 0 : Math.max(0, elevatedUntil - now);
  const elevated = remaining > 0;

  // The agent records a step-up requirement when the PEP refused a tool for LoA < 2.
  async function checkStepUp(text: string) {
    try {
      const res = await fetch('/api/agent/assurance', { cache: 'no-store' });
      if (!res.ok) return;
      const assurance = (await res.json()) as { decision?: string | null };
      if (assurance.decision === 'STEP_UP_REQUIRED') setStepUp({ text, verifying: false });
    } catch {
      // No prompt if the check fails; the agent's own reply still explains it.
    }
  }

  const openStepUp = useCallback(() => {
    const popup = window.open('/api/auth/login?stepup=1', 'verify-step-up', 'popup,width=480,height=640');
    if (!popup) {
      setMessages((prev) => [
        ...prev,
        { role: 'agent', text: 'The verification window was blocked. Allow pop-ups for this site and try again.' },
      ]);
      return;
    }
    setStepUp((cur) => (cur ? { ...cur, verifying: true } : cur));
    // Closing the popup without finishing is a cancel: put the prompt back.
    const timer = window.setInterval(() => {
      if (popup.closed) {
        window.clearInterval(timer);
        setStepUp((cur) => (cur ? { ...cur, verifying: false } : cur));
      }
    }, 500);
  }, []);

  // The popup's callback page reports how the step-up ended.
  useEffect(() => {
    if (!stepUp) return;
    const channel = new BroadcastChannel(STEP_UP_CHANNEL);
    channel.onmessage = (event: MessageEvent<StepUpResult>) => {
      if (event.data === 'complete') {
        const retryText = stepUp.text;
        setStepUp(null);
        void refreshLoa();
        void sendText(retryText, true);
      } else if (event.data === 'failed') {
        setStepUp({ text: stepUp.text, verifying: false });
        setMessages((prev) => [...prev, { role: 'agent', text: 'Verification did not complete.' }]);
      }
    };
    return () => channel.close();
  }, [stepUp, sendText, refreshLoa]);

  const handleClear = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages([]);
    setPending(null);
    setSending(false);
    setInput('');
    composerRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && (e.key === 'l' || e.key === 'L')) {
        e.preventDefault();
        handleClear();
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [handleClear]);

  return (
    <div className="workspace-root">
      <div className="workspace-grid">
        <HeroPanel username={username} msgCount={messages.length} onStart={focusComposer} />
        <MessageLog messages={messages} pending={pending} />
        <div className="workspace-grid__composer">
          {stepUp && (
            <ActionableNotification
              kind="warning"
              lowContrast
              hideCloseButton
              inline
              title="Verification required"
              subtitle={`This action needs a stronger login (level 2). Enter your one-time code in the window that opens; your request is retried automatically. Verification lasts ${Math.round(ttlSeconds / 60)} minutes.`}
              actionButtonLabel={stepUp.verifying ? 'Waiting for verification…' : 'Verify with one-time code'}
              onActionButtonClick={stepUp.verifying ? undefined : openStepUp}
            />
          )}
          {elevated && (
            <div className="workspace-grid__assurance">
              <Tag type="green" size="md">
                Level 2 verified · {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, '0')} left
              </Tag>
            </div>
          )}
          <Composer
            textareaRef={composerRef}
            value={input}
            onChange={setInput}
            onSend={handleSend}
            onClear={handleClear}
            disabled={sending}
          />
        </div>
        <TokenInspector refreshKey={agentTokensRefreshKey} />
      </div>
    </div>
  );
}
