'use client';

import { useEffect, useMemo, useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import type { ServiceRef } from '@/lib/types';

type Props = {
  agents: ServiceRef[];
  agentsError: string | null;
  mcpServers: ServiceRef[];
  mcpServersError: string | null;
  initialVersion: number | null;
  // Pairs already present in the catalog. We use this to nudge operators to
  // the View/Edit page rather than silently overwriting an existing allow list.
  existingPairs: Array<[string, string]>;
};

export function NewRuleForm({
  agents,
  agentsError,
  mcpServers,
  mcpServersError,
  initialVersion,
  existingPairs,
}: Props) {
  const [srcKey, setSrcKey] = useState('');
  const [dstKey, setDstKey] = useState('');
  const [allow, setAllow] = useState<Set<string>>(new Set());
  const [manualTool, setManualTool] = useState('');
  const [adhocTools, setAdhocTools] = useState<string[]>([]);
  const [discoveredTools, setDiscoveredTools] = useState<string[] | null>(null);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [discoveryLoading, setDiscoveryLoading] = useState(false);

  const [submitting, startTransition] = useTransition();
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusKind, setStatusKind] = useState<'success' | 'error' | null>(null);
  const router = useRouter();

  const existingSet = useMemo(
    () => new Set(existingPairs.map(([s, d]) => `${s}|${d}`)),
    [existingPairs],
  );
  const pairExists =
    srcKey && dstKey ? existingSet.has(`${srcKey}|${dstKey}`) : false;

  // Re-fetch discovered tools any time the destination changes. Best-effort:
  // 502/503/404 just means the operator gets a manual-add field instead.
  useEffect(() => {
    if (!dstKey) {
      setDiscoveredTools(null);
      setDiscoveryError(null);
      return;
    }
    const [ns, name] = splitKey(dstKey);
    setDiscoveryLoading(true);
    setDiscoveredTools(null);
    setDiscoveryError(null);
    const ctrl = new AbortController();
    fetch(
      `/api/v1/mcp-servers/${encodeURIComponent(ns)}/${encodeURIComponent(name)}/tools`,
      { signal: ctrl.signal },
    )
      .then(async (res) => {
        const data = await res.json().catch(() => ({}) as Record<string, unknown>);
        if (!res.ok) {
          const detail =
            typeof data === 'object' && data && 'detail' in data
              ? String((data as { detail: unknown }).detail)
              : `Upstream returned ${res.status}`;
          setDiscoveryError(`${res.status}: ${detail}`);
          return;
        }
        const tools = (data as { tools?: unknown }).tools;
        if (Array.isArray(tools)) {
          setDiscoveredTools(
            tools
              .map((t) => (typeof t === 'object' && t && 'name' in t ? String((t as { name: unknown }).name) : null))
              .filter((n): n is string => n !== null),
          );
        } else {
          setDiscoveredTools([]);
        }
      })
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === 'AbortError') return;
        setDiscoveryError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setDiscoveryLoading(false));
    return () => ctrl.abort();
  }, [dstKey]);

  const allRenderedTools = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const t of [...(discoveredTools ?? []), ...adhocTools]) {
      if (seen.has(t)) continue;
      seen.add(t);
      out.push(t);
    }
    return out;
  }, [discoveredTools, adhocTools]);

  function toggle(tool: string) {
    setAllow((prev) => {
      const next = new Set(prev);
      if (next.has(tool)) next.delete(tool);
      else next.add(tool);
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

  const canSubmit = srcKey && dstKey && !pairExists && !submitting;

  function save() {
    if (!srcKey || !dstKey) return;
    const [srcNs, srcSvc] = splitKey(srcKey);
    const [dstNs, dstSvc] = splitKey(dstKey);
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
              `${detail} (Reload this page to pick up the latest catalog version.)`,
            );
          } else {
            setStatusMessage(`${res.status}: ${detail}`);
          }
          return;
        }
        // On success, hop to the View/Edit page — same shape the operator
        // would land on if they navigated from the rules list.
        router.push(
          `/rules/${encodeURIComponent(srcNs)}/${encodeURIComponent(
            srcSvc,
          )}/${encodeURIComponent(dstNs)}/${encodeURIComponent(dstSvc)}`,
        );
        router.refresh();
      } catch (err) {
        setStatusKind('error');
        setStatusMessage(err instanceof Error ? err.message : String(err));
      }
    });
  }

  return (
    <div className="space-y-5">
      <section className="rounded-lg border border-content-border bg-white p-5">
        <h2 className="text-base font-semibold text-content-ink">Source & destination</h2>
        <p className="mt-1 text-xs text-content-muted">
          Sources and destinations come from Consul services annotated with
          <code className="mx-1 rounded bg-content-subtle px-1">agent-tool-authz-role</code>
          service-meta.
        </p>

        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <Picker
            label="Source (agent)"
            value={srcKey}
            options={agents}
            error={agentsError}
            onChange={setSrcKey}
            placeholder={
              agents.length === 0 && !agentsError
                ? 'No agents registered'
                : 'Select an agent…'
            }
          />
          <Picker
            label="Destination (MCP server)"
            value={dstKey}
            options={mcpServers}
            error={mcpServersError}
            onChange={(k) => {
              setDstKey(k);
              setAllow(new Set());
              setAdhocTools([]);
            }}
            placeholder={
              mcpServers.length === 0 && !mcpServersError
                ? 'No MCP servers registered'
                : 'Select an MCP server…'
            }
          />
        </div>

        {pairExists ? (
          <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            A rule already exists for this pair. Edit it on the{' '}
            <a
              href={`/rules/${encodeURIComponent(splitKey(srcKey)[0])}/${encodeURIComponent(
                splitKey(srcKey)[1],
              )}/${encodeURIComponent(splitKey(dstKey)[0])}/${encodeURIComponent(
                splitKey(dstKey)[1],
              )}`}
              className="font-medium underline"
            >
              View/Edit page
            </a>{' '}
            instead — saving here would replace its allow list.
          </div>
        ) : null}
      </section>

      <section className="rounded-lg border border-content-border bg-white p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-content-ink">Allowed tools</h2>
          <span className="text-xs text-content-muted">
            {allow.size} selected
          </span>
        </div>

        {!dstKey ? (
          <p className="mt-3 text-sm text-content-muted">
            Select a destination to load its tools.
          </p>
        ) : discoveryLoading ? (
          <p className="mt-3 text-sm text-content-muted">Loading tools…</p>
        ) : (
          <>
            {discoveryError ? (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                <div className="font-medium">
                  Live tool discovery unavailable — add tool names below manually.
                </div>
                <div className="mt-0.5 font-mono">{discoveryError}</div>
              </div>
            ) : null}

            <ul className="mt-4 divide-y divide-content-divider">
              {allRenderedTools.length === 0 ? (
                <li className="py-3 text-sm text-content-muted">
                  No tools to choose from yet — add a tool name below to allow it.
                </li>
              ) : (
                allRenderedTools.map((tool) => {
                  const id = `new-tool-${tool}`;
                  const discovered = (discoveredTools ?? []).includes(tool);
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
                            (added manually)
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
          </>
        )}
      </section>

      <div className="flex items-center justify-between rounded-lg border border-content-border bg-white p-5">
        <div className="text-sm">
          {statusMessage ? (
            <span
              className={
                statusKind === 'error' ? 'text-rose-700' : 'text-emerald-700'
              }
            >
              {statusMessage}
            </span>
          ) : (
            <span className="text-content-muted">
              {initialVersion === null
                ? 'Catalog version unavailable — save will write without CAS.'
                : `Saves use expected_version=${initialVersion} (CAS).`}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={save}
          disabled={!canSubmit}
          className="btn-primary disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? 'Saving…' : 'Create rule'}
        </button>
      </div>
    </div>
  );
}

function Picker({
  label,
  value,
  options,
  error,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  options: ServiceRef[];
  error: string | null;
  onChange: (key: string) => void;
  placeholder: string;
}) {
  return (
    <label className="block">
      <span className="block text-xs font-medium uppercase tracking-wide text-content-muted">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 h-9 w-full rounded-md border border-content-border bg-white px-2 text-sm text-content-ink focus:border-brand focus:outline-none"
      >
        <option value="">{placeholder}</option>
        {options.map((opt) => {
          const key = `${opt.namespace}/${opt.name}`;
          return (
            <option key={key} value={key}>
              {opt.name} · {opt.namespace}
            </option>
          );
        })}
      </select>
      {error ? (
        <span className="mt-1 block font-mono text-xs text-amber-900">
          {error}
        </span>
      ) : null}
    </label>
  );
}

function splitKey(key: string): [string, string] {
  const slash = key.indexOf('/');
  if (slash < 0) return ['default', key];
  return [key.slice(0, slash), key.slice(slash + 1)];
}
