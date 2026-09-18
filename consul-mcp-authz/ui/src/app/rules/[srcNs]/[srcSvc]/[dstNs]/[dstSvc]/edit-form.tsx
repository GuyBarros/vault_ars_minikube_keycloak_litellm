'use client';

import { useMemo, useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';

type Props = {
  srcNs: string;
  srcSvc: string;
  dstNs: string;
  dstSvc: string;
  initialAllow: string[];
  initialVersion: number;
  // null when discovery failed (503/502/404). The form still works — the
  // operator can keep the existing allow list and add tool names manually.
  discoveredTools: string[] | null;
  discoveryError: string | null;
};

export function EditAllowForm({
  srcNs,
  srcSvc,
  dstNs,
  dstSvc,
  initialAllow,
  initialVersion,
  discoveredTools,
  discoveryError,
}: Props) {
  const initialSet = useMemo(() => new Set(initialAllow), [initialAllow]);
  const [allow, setAllow] = useState<Set<string>>(initialSet);
  const [manualTool, setManualTool] = useState('');
  // Tools currently in the allow list that discovery doesn't know about —
  // either because discovery is unavailable, or because the operator added
  // them ahead of the MCP server being deployed. Either way we render them
  // as checkboxes alongside the discovered set so they can be toggled.
  const extras = useMemo(() => {
    const discovered = new Set(discoveredTools ?? []);
    return Array.from(initialSet).filter((t) => !discovered.has(t));
  }, [initialSet, discoveredTools]);
  // Plus any extras the operator typed in this session.
  const [adhocTools, setAdhocTools] = useState<string[]>([]);

  const allRenderedTools = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const t of [...(discoveredTools ?? []), ...extras, ...adhocTools]) {
      if (seen.has(t)) continue;
      seen.add(t);
      out.push(t);
    }
    return out;
  }, [discoveredTools, extras, adhocTools]);

  const [submitting, startTransition] = useTransition();
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusKind, setStatusKind] = useState<'success' | 'error' | null>(null);
  const router = useRouter();

  const dirty = useMemo(() => {
    if (allow.size !== initialSet.size) return true;
    for (const t of allow) {
      if (!initialSet.has(t)) return true;
    }
    return false;
  }, [allow, initialSet]);

  function toggle(tool: string) {
    setAllow((prev) => {
      const next = new Set(prev);
      if (next.has(tool)) {
        next.delete(tool);
      } else {
        next.add(tool);
      }
      return next;
    });
  }

  function addManualTool() {
    const trimmed = manualTool.trim();
    if (!trimmed) return;
    setAdhocTools((prev) => (prev.includes(trimmed) ? prev : [...prev, trimmed]));
    setAllow((prev) => new Set(prev).add(trimmed));
    setManualTool('');
  }

  function save() {
    setStatusMessage(null);
    setStatusKind(null);
    startTransition(async () => {
      try {
        const res = await fetch(
          `/api/v1/rules/${encodeURIComponent(srcNs)}/${encodeURIComponent(
            srcSvc,
          )}/${encodeURIComponent(dstNs)}/${encodeURIComponent(dstSvc)}`,
          {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              allow: Array.from(allow).sort(),
              expected_version: initialVersion,
            }),
          },
        );
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
        setStatusKind('success');
        setStatusMessage(
          `Saved as version ${(data as { version?: number }).version ?? '?'}.`,
        );
        // Refetch the server component so the page reflects the new
        // version + allow list without a full reload.
        router.refresh();
      } catch (err) {
        setStatusKind('error');
        setStatusMessage(err instanceof Error ? err.message : String(err));
      }
    });
  }

  return (
    <section className="rounded-lg border border-content-border bg-white p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-content-ink">Allowed tools</h2>
        <span className="text-xs text-content-muted">
          {allow.size} of {allRenderedTools.length || allow.size} selected
        </span>
      </div>

      {discoveryError ? (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <div className="font-medium">
            Live tool discovery unavailable — using existing allow list.
          </div>
          <div className="mt-0.5 font-mono">{discoveryError}</div>
        </div>
      ) : null}

      <ul className="mt-4 divide-y divide-content-divider">
        {allRenderedTools.length === 0 ? (
          <li className="py-3 text-sm text-content-muted">
            No tools available — discovery returned an empty list and no tools
            are currently in the allow list. Add a tool name below to allow it.
          </li>
        ) : (
          allRenderedTools.map((tool) => {
            const discovered = (discoveredTools ?? []).includes(tool);
            const id = `tool-${tool}`;
            return (
              <li key={tool} className="flex items-center gap-3 py-2.5">
                <input
                  id={id}
                  type="checkbox"
                  checked={allow.has(tool)}
                  onChange={() => toggle(tool)}
                  disabled={submitting}
                  className="h-4 w-4 rounded border-content-border text-brand focus:ring-brand-ring"
                />
                <label htmlFor={id} className="flex-1 text-sm text-content-ink">
                  <span className="font-mono">{tool}</span>
                  {!discovered ? (
                    <span className="ml-2 text-xs text-content-muted">
                      (not in current tools/list — added manually)
                    </span>
                  ) : null}
                </label>
              </li>
            );
          })
        )}
      </ul>

      <div className="mt-4 flex items-center gap-2 border-t border-content-divider pt-4">
        <input
          type="text"
          value={manualTool}
          onChange={(e) => setManualTool(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              addManualTool();
            }
          }}
          placeholder="Add tool name (advanced)"
          disabled={submitting}
          className="h-9 flex-1 rounded-md border border-content-border bg-white px-3 text-sm text-content-ink placeholder:text-content-muted focus:border-brand focus:outline-none"
        />
        <button
          type="button"
          onClick={addManualTool}
          disabled={submitting || !manualTool.trim()}
          className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50"
        >
          Add
        </button>
      </div>

      <div className="mt-5 flex items-center justify-between border-t border-content-divider pt-4">
        <div className="text-sm">
          {statusMessage ? (
            <span
              className={
                statusKind === 'error'
                  ? 'text-rose-700'
                  : 'text-emerald-700'
              }
            >
              {statusMessage}
            </span>
          ) : (
            <span className="text-content-muted">
              Changes save via PATCH with optimistic concurrency (version{' '}
              {initialVersion}).
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={save}
          disabled={submitting || !dirty}
          className="btn-primary disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </section>
  );
}
