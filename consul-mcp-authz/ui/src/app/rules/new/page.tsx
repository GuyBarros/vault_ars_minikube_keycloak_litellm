import Link from 'next/link';
import { Sidebar } from '@/components/sidebar';
import { ChevronLeftIcon } from '@/components/icons';
import { api, ApiError } from '@/lib/api';
import { NewRuleForm } from './new-rule-form';
import type { ServiceRef } from '@/lib/types';

export const dynamic = 'force-dynamic';

export default async function NewRulePage() {
  // We need three independent reads to populate the form. All are best-effort
  // — if one fails we still render the form and surface the failure inline so
  // the operator knows whether pickers will be populated from Consul or empty.
  let agents: ServiceRef[] = [];
  let agentsError: string | null = null;
  let mcpServers: ServiceRef[] = [];
  let mcpServersError: string | null = null;
  let version: number | null = null;
  let existingPairs: Array<[string, string]> = [];
  let rulesError: string | null = null;

  await Promise.all([
    api
      .getAgents()
      .then((d) => {
        agents = d.agents;
      })
      .catch((err: unknown) => {
        agentsError = formatErr(err);
      }),
    api
      .getMcpServers()
      .then((d) => {
        mcpServers = d.mcp_servers;
      })
      .catch((err: unknown) => {
        mcpServersError = formatErr(err);
      }),
    api
      .getRules()
      .then((d) => {
        version = d.version;
        for (const [src, dests] of Object.entries(d.catalog.rules ?? {})) {
          for (const dst of Object.keys(dests ?? {})) {
            existingPairs.push([src, dst]);
          }
        }
      })
      .catch((err: unknown) => {
        rulesError = formatErr(err);
      }),
  ]);

  return (
    <div className="flex min-h-screen">
      <Sidebar activeHref="/rules" />
      <main className="min-w-0 flex-1 bg-content-bg">
        <div className="mx-auto max-w-[900px] px-10 py-10">
          <header className="mb-6">
            <Link
              href="/rules"
              className="inline-flex items-center gap-1 text-sm text-content-muted hover:text-content-ink"
            >
              <ChevronLeftIcon size={14} />
              Rules
            </Link>
            <h1 className="mt-2 text-3xl font-bold tracking-tight text-content-ink">
              New rule
            </h1>
            <p className="mt-1 text-sm text-content-muted">
              Allow a source service to call selected tools on a destination
              MCP server. Saved via PATCH with optimistic concurrency
              {version !== null ? ` (catalog version ${version})` : ''}.
            </p>
          </header>

          {rulesError ? (
            <Banner tone="error" title="Could not load catalog">
              {rulesError}
            </Banner>
          ) : null}

          <NewRuleForm
            agents={agents}
            agentsError={agentsError}
            mcpServers={mcpServers}
            mcpServersError={mcpServersError}
            initialVersion={version}
            existingPairs={existingPairs}
          />
        </div>
      </main>
    </div>
  );
}

function formatErr(err: unknown): string {
  if (err instanceof ApiError) {
    return `${err.status}: ${err.message}`;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return 'Unknown error';
}

function Banner({
  tone,
  title,
  children,
}: {
  tone: 'error' | 'warn';
  title: string;
  children: React.ReactNode;
}) {
  const palette =
    tone === 'error'
      ? 'border-amber-200 bg-amber-50 text-amber-900'
      : 'border-amber-200 bg-amber-50 text-amber-900';
  return (
    <div className={`mb-6 rounded-md border px-4 py-3 text-sm ${palette}`}>
      <div className="font-semibold">{title}</div>
      <div className="mt-1 font-mono text-xs">{children}</div>
    </div>
  );
}
