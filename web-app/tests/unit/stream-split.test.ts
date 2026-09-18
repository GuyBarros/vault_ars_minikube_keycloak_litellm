import { describe, it, expect } from 'vitest';
import { splitStreamBuffer } from '@/lib/agent/stream';

describe('splitStreamBuffer', () => {
  it('returns full buffer when no trailing backslash', () => {
    expect(splitStreamBuffer('hello')).toEqual(['hello', '']);
  });

  it('holds back a single trailing backslash', () => {
    expect(splitStreamBuffer('foo\\')).toEqual(['foo', '\\']);
  });

  it('treats two trailing backslashes as a complete escape', () => {
    expect(splitStreamBuffer('foo\\\\')).toEqual(['foo\\\\', '']);
  });

  it('holds back an odd count of trailing backslashes', () => {
    expect(splitStreamBuffer('foo\\\\\\')).toEqual(['foo\\\\', '\\']);
  });

  it('handles empty buffer', () => {
    expect(splitStreamBuffer('')).toEqual(['', '']);
  });

  it('handles only-backslashes buffer', () => {
    expect(splitStreamBuffer('\\')).toEqual(['', '\\']);
    expect(splitStreamBuffer('\\\\')).toEqual(['\\\\', '']);
  });
});
