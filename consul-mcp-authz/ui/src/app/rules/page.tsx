import Link from 'next/link';
import { Sidebar } from '@/components/sidebar';
import { RulesTable } from '@/components/rules-table';
import { SearchIcon } from '@/components/icons';
import { api, ApiError } from '@/lib/api';
import { flattenCatalog } from '@/lib/types';

// Always fetch live data; the catalog can change underneath us at any
// moment from the API itself, so a stale render would mislead operators.
export const dynamic = 'force-dynamic';

export default async function RulesPage() {
  let rows: ReturnType<typeof flattenCatalog> = [];
  let version: number | null = null;
  let createdTime: string | null = null;
  let errorMessage: string | null = null;

  try {
    const data = await api.getRules();
    rows = flattenCatalog(data.catalog);
    version = data.version;
    createdTime = data.created_time;
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
      <Sidebar activeHref="/rules" />

      <main className="min-w-0 flex-1 bg-content-bg">
        <div className="mx-auto max-w-[1280px] px-10 py-10">
          <header className="flex items-end justify-between">
            <div>
              <h1 className="text-3xl font-bold tracking-tight text-content-ink">
                Rules{' '}
                <span className="ml-2 align-middle text-base font-normal text-content-muted">
                  {rows.length} total
                </span>
              </h1>
              {version !== null ? (
                <p className="mt-1 text-sm text-content-muted">
                  Catalog version {version}
                  {createdTime ? ` · written ${formatTime(createdTime)}` : ''}
                </p>
              ) : null}
            </div>
            <Link href="/rules/new" className="btn-primary">
              Create
            </Link>
          </header>

          <Toolbar />

          {errorMessage ? <ErrorBanner message={errorMessage} /> : null}
          {!errorMessage ? <RulesTable rows={rows} /> : null}
        </div>
      </main>
    </div>
  );
}

function Toolbar() {
  return (
    <div className="mt-6 mb-4 flex items-center gap-3">
      <label className="relative flex-1">
        <SearchIcon
          size={14}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-content-muted"
        />
        <input
          type="search"
          placeholder="Search"
          disabled
          className="h-9 w-full rounded-md border border-content-border bg-white pl-8 pr-3 text-sm text-content-ink placeholder:text-content-muted focus:border-brand focus:outline-none"
        />
      </label>
      <button
        type="button"
        disabled
        className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50"
      >
        Search Across
      </button>
      <button
        type="button"
        disabled
        className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50"
      >
        Permission
      </button>
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <div className="font-semibold">Could not load rules</div>
      <div className="mt-1 font-mono text-xs">{message}</div>
      <div className="mt-2 text-xs text-amber-800/80">
        Make sure <code>consul-mcp-authz</code> is reachable. For local
        dev, <code>kubectl port-forward deploy/consul-mcp-authz 8080:8080</code>{' '}
        and run the UI with{' '}
        <code>MCP_AUTHZ_UI_API_URL=http://localhost:8080</code>.
      </div>
    </div>
  );
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
