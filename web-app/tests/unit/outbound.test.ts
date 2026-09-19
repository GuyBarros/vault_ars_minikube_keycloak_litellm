import { describe, it, expect } from 'vitest';
import {
  REQUEST_ID_HEADER_NAME,
  runWithRequestContext,
} from '@/lib/log/context';
import { buildOutboundHeaders } from '@/lib/log/outbound';

describe('buildOutboundHeaders', () => {
  it('propagates the bound request id', () => {
    const headers = runWithRequestContext(
      {
        request_id: 'req-1234',
        client_ip: '10.0.0.1',
        request_path: '/test',
        preferred_username: '',
      },
      () => buildOutboundHeaders({ Authorization: 'Bearer x' }),
    );

    expect(headers[REQUEST_ID_HEADER_NAME]).toBe('req-1234');
    expect(headers.Authorization).toBe('Bearer x');
  });

  it('generates a request id when context is missing', () => {
    const headers = buildOutboundHeaders();
    expect(headers[REQUEST_ID_HEADER_NAME]).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    );
  });
});

describe('buildOutboundHeaders', () => {
  it('propagates the bound request id', () => {
    const headers = runWithRequestContext(
      {
        request_id: 'req-1234',
        client_ip: '10.0.0.1',
        request_path: '/test',
        preferred_username: '',
      },
      () => buildOutboundHeaders({ Authorization: 'Bearer x' }),
    );

    expect(headers[REQUEST_ID_HEADER_NAME]).toBe('req-1234');
    expect(headers.Authorization).toBe('Bearer x');
  });

  it('generates a request id when context is missing', () => {
    const headers = buildOutboundHeaders();
    expect(headers[REQUEST_ID_HEADER_NAME]).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    );
  });
});
