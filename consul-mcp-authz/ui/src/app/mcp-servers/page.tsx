import { Sidebar } from '@/components/sidebar';
import { ServicesTable } from '@/components/services-table';
import { api, ApiError } from '@/lib/api';
import type { ServiceRef } from '@/lib/types';

export const dynamic = 'force-dynamic';

export default async function McpServersPage() {
  let mcpServers: ServiceRef[] = [];
  let errorMessage: string | null = null;

  try {
    const data = await api.getMcpServers();
    mcpServers = data.mcp_servers;
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
      <Sidebar activeHref="/mcp-servers" />
      <main className="min-w-0 flex-1 bg-content-bg">
        <div className="mx-auto max-w-[1100px] px-10 py-10">
          <header>
            <h1 className="text-3xl font-bold tracking-tight text-content-ink">
              MCP Servers{' '}
              <span className="ml-2 align-middle text-base font-normal text-content-muted">
                {mcpServers.length} total
              </span>
            </h1>
            <p className="mt-1 text-sm text-content-muted">
              Consul services annotated with{' '}
              <code className="rounded bg-content-subtle px-1">
                agent-tool-authz-role=mcp-server
              </code>
              . Use these as destinations when creating rules.
            </p>
          </header>

          <div className="mt-6">
            {errorMessage ? (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <div className="font-semibold">Could not load MCP servers</div>
                <div className="mt-1 font-mono text-xs">{errorMessage}</div>
              </div>
            ) : (
              <ServicesTable
                services={mcpServers}
                emptyTitle="No MCP servers registered"
                emptyBody="No Consul services carry the agent-tool-authz-role=mcp-server service-meta yet."
                linkFor={(s) =>
                  `/mcp-servers/${encodeURIComponent(s.namespace)}/${encodeURIComponent(s.name)}`
                }
              />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
