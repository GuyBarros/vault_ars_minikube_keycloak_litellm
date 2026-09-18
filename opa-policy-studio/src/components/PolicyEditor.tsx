import Editor from '@monaco-editor/react'
import { Save } from 'lucide-react'
import { useCallback, useEffect } from 'react'
import { toast } from 'react-hot-toast'
import { registerRegoLanguage } from '../lib/regoLanguage'
import { useAppStore } from '../store/useAppStore'

export function PolicyEditor() {
  const selectedPolicyId = useAppStore((s) => s.selectedPolicyId)
  const policyContent = useAppStore((s) => s.policyContent)
  const savingPolicy = useAppStore((s) => s.savingPolicy)
  const theme = useAppStore((s) => s.theme)
  const setPolicyContent = useAppStore((s) => s.setPolicyContent)
  const savePolicy = useAppStore((s) => s.savePolicy)

  const onSave = useCallback(async () => {
    try {
      await savePolicy()
      toast.success('Policy saved')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to save policy')
    }
  }, [savePolicy])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        void onSave()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onSave])

  return (
    <section className="flex h-full min-w-0 flex-col bg-surface/80">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-widest text-subtle">Policy Editor</h2>
          <p className="text-xs text-muted">{selectedPolicyId ?? 'No policy selected'}</p>
        </div>
        <button
          onClick={() => void onSave()}
          disabled={!selectedPolicyId || savingPolicy}
          className="inline-flex items-center gap-2 rounded-md border border-border bg-panel px-3 py-1.5 text-sm text-foreground hover:border-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Save size={14} />
          {savingPolicy ? 'Saving...' : 'Save'}
        </button>
      </div>

      <div className="min-h-0 flex-1">
        <Editor
          height="100%"
          defaultLanguage="rego"
          language="rego"
          theme={theme === 'dark' ? 'vs-dark' : 'vs'}
          beforeMount={registerRegoLanguage}
          value={policyContent}
          onChange={(value) => setPolicyContent(value ?? '')}
          options={{
            minimap: { enabled: false },
            fontSize: 14,
            lineNumbersMinChars: 3,
            wordWrap: 'on',
            smoothScrolling: true,
            scrollBeyondLastLine: false,
            tabSize: 2,
          }}
        />
      </div>
    </section>
  )
}
