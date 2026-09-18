// @ts-nocheck
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  computeRetryDelayMs,
  getAgentTokens,
  invokeStream,
  isRetryableAgentNetworkError,
} from '@/lib/agent/client';

function dnsError(): Error {
  return new Error('fetch failed', {
    cause: new Error('getaddrinfo ENOTFOUND ai-agent.virtual.consul'),
  });
}

describe('agent client retry behavior', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(global.Math, 'random').mockReturnValue(0);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('classifies transient DNS errors as retryable', () => {
    expect(isRetryableAgentNetworkError(dnsError())).toBe(true);
    expect(isRetryableAgentNetworkError(new Error('connect ECONNREFUSED 127.0.0.1:8080'))).toBe(false);
  });

  it('computes bounded exponential retry delay with jitter', () => {
    expect(computeRetryDelayMs(1, { maxAttempts: 3, baseDelayMs: 150, maxDelayMs: 1000 }, 0)).toBe(150);
    expect(computeRetryDelayMs(2, { maxAttempts: 3, baseDelayMs: 150, maxDelayMs: 1000 }, 0)).toBe(300);
    expect(computeRetryDelayMs(4, { maxAttempts: 6, baseDelayMs: 500, maxDelayMs: 1000 }, 0)).toBe(1000);
  });

  it('retries token fetch and succeeds on a later attempt', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(dnsError())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ actor_token: 'actor', obo_token: null }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    vi.stubGlobal('fetch', fetchMock);

    const resultPromise = getAgentTokens('token');
    await vi.runAllTimersAsync();
    await expect(resultPromise).resolves.toEqual({ actor_token: 'actor', obo_token: null });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('fails after max retry attempts for stream invocation', async () => {
    const fetchMock = vi.fn().mockRejectedValue(dnsError());
    vi.stubGlobal('fetch', fetchMock);

    const resultPromise = invokeStream({
      message: 'hello',
      history: [],
      accessToken: 'token',
    });

    const rejection = expect(resultPromise).rejects.toThrow(/could not be resolved/i);
    await vi.runAllTimersAsync();
    await rejection;
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
