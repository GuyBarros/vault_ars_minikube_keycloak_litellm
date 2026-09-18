import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Sidebar } from '@/components/sidebar';
import { ChevronLeftIcon, NamespaceIcon, ServiceIcon } from '@/components/icons';
import { api, ApiError } from '@/lib/api';
import type { ToolInfo } from '@/lib/types';

export const dynamic = 'force-dynamic';

type Params = { ns: string; name: string };

export default async function McpServerDetailPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { ns, name } = await params;

  let tools: ToolInfo[] = [];
  let errorMessage: string | null = null;

  try {
    const data = await api.getMcpServerTools(ns, name);
    tools = data.tools;
  } catch (err) {
    // 404 means the API confirmed this isn't a registered mcp-server — show
    // Next's notFound page so the URL feels honest.
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    errorMessage =
      err instanceof ApiError
        ? `${err.status}: ${err.message}`
        : err instanceof Error
          ? err.message
          : 'Unknown discovery error';
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar activeHref="/mcp-servers" />
      <main className="min-w-0 flex-1 bg-content-bg">
        <div className="mx-auto max-w-[1100px] px-10 py-10">
          <header className="mb-6">
            <Link
              href="/mcp-servers"
              className="inline-flex items-center gap-1 text-sm text-content-muted hover:text-content-ink"
            >
              <ChevronLeftIcon size={14} />
              MCP Servers
            </Link>
            <h1 className="mt-2 text-3xl font-bold tracking-tight text-content-ink">
              {name}
              {errorMessage ? null : (
                <span className="ml-2 align-middle text-base font-normal text-content-muted">
                  {tools.length} {tools.length === 1 ? 'tool' : 'tools'}
                </span>
              )}
            </h1>
            <div className="mt-2 flex items-center gap-3 text-xs text-content-muted">
              <span className="inline-flex items-center gap-1">
                <ServiceIcon size={12} />
                {name}
              </span>
              <span className="inline-flex items-center gap-1">
                <NamespaceIcon size={12} />
                {ns}
              </span>
            </div>
            <p className="mt-3 text-sm text-content-muted">
              Tools advertised by this MCP server&rsquo;s live{' '}
              <code className="rounded bg-content-subtle px-1">tools/list</code>{' '}
              response over the Consul mesh.
            </p>
          </header>

          {errorMessage ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <div className="font-semibold">Could not load tools</div>
              <div className="mt-1 font-mono text-xs">{errorMessage}</div>
            </div>
          ) : tools.length === 0 ? (
            <div className="rounded-lg border border-dashed border-content-border bg-white px-6 py-16 text-center">
              <h2 className="text-base font-semibold text-content-ink">
                No tools advertised
              </h2>
              <p className="mx-auto mt-2 max-w-md text-sm text-content-muted">
                This MCP server&rsquo;s <code>tools/list</code> returned an empty
                set. Add a tool via <code>@mcp.tool</code> in the server and
                redeploy.
              </p>
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border border-content-border bg-white">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="bg-content-subtle text-left text-xs font-medium uppercase tracking-wide text-content-muted">
                    <th className="w-1/3 px-4 py-3 font-medium">Name</th>
                    <th className="px-4 py-3 font-medium">Description</th>
                  </tr>
                </thead>
                <tbody>
                  {tools.map((tool, i) => (
                    <tr
                      key={tool.name}
                      className={
                        i === tools.length - 1
                          ? ''
                          : 'border-b border-content-divider'
                      }
                    >
                      <td className="px-4 py-3 align-top font-mono text-content-ink">
                        {tool.name}
                      </td>
                      <td className="px-4 py-3 align-top text-content-ink">
                        {tool.description ? (
                          <span className="whitespace-pre-wrap">
                            {tool.description}
                          </span>
                        ) : (
                          <span className="italic text-content-muted">
                            No description provided.
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
