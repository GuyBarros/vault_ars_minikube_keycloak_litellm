'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import type { VersionInfo } from '@/lib/types';

type Props = {
  versions: VersionInfo[];
  currentVersion: number;
};

export function HistoryTable({ versions, currentVersion }: Props) {
  const [pendingVersion, setPendingVersion] = useState<number | null>(null);
  const [submitting, startTransition] = useTransition();
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusKind, setStatusKind] = useState<'success' | 'error' | null>(null);
  const router = useRouter();

  if (versions.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-content-border bg-white px-6 py-16 text-center">
        <h2 className="text-base font-semibold text-content-ink">No versions yet</h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-content-muted">
          The catalog has no recorded history. Once a rule is created or edited,
          Vault KV v2 will start tracking versions here.
        </p>
      </div>
    );
  }

  function rollback(version: number) {
    setStatusMessage(null);
    setStatusKind(null);
    startTransition(async () => {
      try {
        const res = await fetch('/api/v1/rules/rollback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ version, expected_version: currentVersion }),
        });
        const data = await res.json().catch(() => ({}) as Record<string, unknown>);
        if (!res.ok) {
          const detail =
            typeof data === 'object' && data && 'detail' in data
              ? String((data as { detail: unknown }).detail)
              : `Upstream returned ${res.status}`;
          setStatusKind('error');
          if (res.status === 412) {
            setStatusMessage(
              `${detail} (Reload to pick up the latest version.)`,
            );
          } else {
            setStatusMessage(`${res.status}: ${detail}`);
          }
          return;
        }
        const newVersion = (data as { version?: number }).version;
        setStatusKind('success');
        setStatusMessage(
          `Rolled back to v${version} — saved as new version ${newVersion ?? '?'}.`,
        );
        setPendingVersion(null);
        router.refresh();
      } catch (err) {
        setStatusKind('error');
        setStatusMessage(err instanceof Error ? err.message : String(err));
      }
    });
  }

  return (
    <div className="space-y-3">
      {statusMessage ? (
        <div
          className={`rounded-md border px-3 py-2 text-sm ${
            statusKind === 'error'
              ? 'border-rose-200 bg-rose-50 text-rose-800'
              : 'border-emerald-200 bg-emerald-50 text-emerald-800'
          }`}
        >
          {statusMessage}
        </div>
      ) : null}

      <div className="overflow-hidden rounded-lg border border-content-border bg-white">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-content-subtle text-left text-xs font-medium uppercase tracking-wide text-content-muted">
              <th className="px-4 py-3 font-medium">Version</th>
              <th className="px-4 py-3 font-medium">Created</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((v, i) => {
              const isCurrent = v.version === currentVersion;
              const isDestroyed = v.destroyed;
              const isDeleted = !!v.deletion_time;
              const isPending = pendingVersion === v.version;
              return (
                <tr
                  key={v.version}
                  className={i === versions.length - 1 ? '' : 'border-b border-content-divider'}
                >
                  <td className="px-4 py-3 align-top">
                    <span className="font-mono text-sm text-content-ink">v{v.version}</span>
                  </td>
                  <td className="px-4 py-3 align-top text-content-muted">
                    {formatTime(v.created_time)}
                  </td>
                  <td className="px-4 py-3 align-top">
                    {isCurrent ? (
                      <span className="pill-allow">Current</span>
                    ) : isDestroyed ? (
                      <span className="pill-status text-content-muted">Destroyed</span>
                    ) : isDeleted ? (
                      <span className="pill-status text-content-muted">Deleted</span>
                    ) : (
                      <span className="pill-status">Available</span>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top text-right">
                    {isCurrent || isDestroyed ? (
                      <span className="text-xs text-content-muted">—</span>
                    ) : isPending ? (
                      <div className="flex items-center justify-end gap-2">
                        <span className="text-xs text-content-muted">
                          Roll back to v{v.version}?
                        </span>
                        <button
                          type="button"
                          onClick={() => setPendingVersion(null)}
                          disabled={submitting}
                          className="btn-secondary text-xs disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => rollback(v.version)}
                          disabled={submitting}
                          className="btn-primary text-xs disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {submitting ? 'Rolling back…' : 'Confirm'}
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setPendingVersion(v.version)}
                        disabled={submitting || isDeleted}
                        className="btn-secondary text-xs disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Roll back
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
