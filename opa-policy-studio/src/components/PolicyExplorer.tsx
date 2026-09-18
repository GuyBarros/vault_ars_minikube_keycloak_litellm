import { Plus, RefreshCw, Trash2 } from 'lucide-react'
import { useMemo } from 'react'
import { toast } from 'react-hot-toast'
import { useAppStore } from '../store/useAppStore'

export function PolicyExplorer() {
  const policies = useAppStore((s) => s.policies)
  const selectedPolicyId = useAppStore((s) => s.selectedPolicyId)
  const loadingPolicies = useAppStore((s) => s.loadingPolicies)
  const lastPolicyLoadError = useAppStore((s) => s.lastPolicyLoadError)
  const loadPolicies = useAppStore((s) => s.loadPolicies)
  const selectPolicy = useAppStore((s) => s.selectPolicy)
  const createPolicy = useAppStore((s) => s.createPolicy)
  const removePolicy = useAppStore((s) => s.removePolicy)

  const sortedPolicies = useMemo(
    () => [...policies].sort((a, b) => a.id.localeCompare(b.id)),
    [policies],
  )

  const onRefresh = async () => {
    try {
      await loadPolicies()
      toast.success('Policies refreshed')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to refresh policies')
    }
  }

  const onCreate = async () => {
    const id = window.prompt('Policy ID (example: app/security.rego)')
    const normalized = id?.trim()
    if (!normalized) return
    try {
      await createPolicy(normalized)
      toast.success(`Policy "${normalized}" created`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to create policy')
    }
  }

  const onDelete = async (id: string) => {
    if (!window.confirm(`Delete policy "${id}"?`)) return
    try {
      await removePolicy(id)
      toast.success(`Policy "${id}" deleted`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to delete policy')
    }
  }

  return (
    <aside className="flex h-full flex-col border-r border-border bg-panel/80">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-subtle">Policies</h2>
        <div className="flex items-center gap-2">
          <button
            onClick={onCreate}
            className="rounded-md border border-border bg-surface px-2 py-1 text-xs text-foreground hover:border-accent"
            title="Create Policy"
          >
            <Plus size={14} />
          </button>
          <button
            onClick={onRefresh}
            className="rounded-md border border-border bg-surface px-2 py-1 text-xs text-foreground hover:border-accent"
            title="Refresh"
          >
            <RefreshCw size={14} className={loadingPolicies ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-2">
        {lastPolicyLoadError && (
          <div className="mb-3 rounded-md border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
            {lastPolicyLoadError}
          </div>
        )}
        {sortedPolicies.length === 0 && (
          <p className="p-3 text-sm text-muted">No policies found. Create one to get started.</p>
        )}
        {sortedPolicies.map((policy) => (
          <div
            key={policy.id}
            className={`mb-1 flex items-center justify-between rounded-md px-2 py-2 text-sm ${
              selectedPolicyId === policy.id
                ? 'bg-accent/20 text-foreground'
                : 'text-subtle hover:bg-panel/70'
            }`}
          >
            <button
              onClick={() => void selectPolicy(policy.id).catch((err: unknown) => {
                toast.error(err instanceof Error ? err.message : 'Failed to open policy')
              })}
              className="min-w-0 flex-1 truncate text-left"
              title={policy.id}
            >
              {policy.id}
            </button>
            <button
              className="ml-2 rounded p-1 text-subtle hover:bg-danger/20 hover:text-danger"
              onClick={() => void onDelete(policy.id)}
              title="Delete"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
    </aside>
  )
}
