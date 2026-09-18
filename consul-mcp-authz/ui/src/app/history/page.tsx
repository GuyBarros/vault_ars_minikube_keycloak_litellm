import { Sidebar } from '@/components/sidebar';
import { api, ApiError } from '@/lib/api';
import type { VersionInfo } from '@/lib/types';
import { HistoryTable } from './history-table';

export const dynamic = 'force-dynamic';

export default async function HistoryPage() {
  let versions: VersionInfo[] = [];
  let currentVersion: number | null = null;
  let errorMessage: string | null = null;

  try {
    const data = await api.getHistory();
    versions = data.versions;
    currentVersion = data.current_version;
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
      <Sidebar activeHref="/history" />
      <main className="min-w-0 flex-1 bg-content-bg">
        <div className="mx-auto max-w-[1100px] px-10 py-10">
          <header>
            <h1 className="text-3xl font-bold tracking-tight text-content-ink">
              History{' '}
              <span className="ml-2 align-middle text-base font-normal text-content-muted">
                {versions.length} {versions.length === 1 ? 'version' : 'versions'}
              </span>
            </h1>
            {currentVersion !== null ? (
              <p className="mt-1 text-sm text-content-muted">
                Current catalog version {currentVersion}. Rolling back creates a
                new version with the contents of the selected one.
              </p>
            ) : null}
          </header>

          <div className="mt-6">
            {errorMessage ? (
              <ErrorBanner message={errorMessage} />
            ) : (
              <HistoryTable
                versions={versions}
                currentVersion={currentVersion ?? 0}
              />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <div className="font-semibold">Could not load history</div>
      <div className="mt-1 font-mono text-xs">{message}</div>
    </div>
  );
}
