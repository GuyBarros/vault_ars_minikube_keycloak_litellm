import { Sidebar } from '@/components/sidebar';
import { ServicesTable } from '@/components/services-table';
import { api, ApiError } from '@/lib/api';
import type { ServiceRef } from '@/lib/types';

export const dynamic = 'force-dynamic';

export default async function AgentsPage() {
  let agents: ServiceRef[] = [];
  let errorMessage: string | null = null;

  try {
    const data = await api.getAgents();
    agents = data.agents;
  } catch (err) {
    errorMessage =
      err instanceof ApiError
        ? `${err.status}: ${err.message}`
        : err instanceof Error
          ? err.message
          : 'Unknown error contacting consul-mcp-authz';
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar activeHref="/agents" />
      <main className="min-w-0 flex-1 bg-content-bg">
        <div className="mx-auto max-w-[1100px] px-10 py-10">
          <header>
            <h1 className="text-3xl font-bold tracking-tight text-content-ink">
              Agents{' '}
              <span className="ml-2 align-middle text-base font-normal text-content-muted">
                {agents.length} total
              </span>
            </h1>
            <p className="mt-1 text-sm text-content-muted">
              Consul services annotated with{' '}
              <code className="rounded bg-content-subtle px-1">
                agent-tool-authz-role=agent
              </code>
              . Use these as sources when creating rules.
            </p>
          </header>

          <div className="mt-6">
            {errorMessage ? (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <div className="font-semibold">Could not load agents</div>
                <div className="mt-1 font-mono text-xs">{errorMessage}</div>
              </div>
            ) : (
              <ServicesTable
                services={agents}
                emptyTitle="No agents registered"
                emptyBody="No Consul services carry the agent-tool-authz-role=agent service-meta yet."
              />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
