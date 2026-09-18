import { Moon, Sun } from 'lucide-react'
import { useEffect } from 'react'
import { Toaster, toast } from 'react-hot-toast'
import { EvaluationPanel } from './components/EvaluationPanel'
import { PolicyEditor } from './components/PolicyEditor'
import { PolicyExplorer } from './components/PolicyExplorer'
import { getOpaBaseUrl } from './lib/opaClient'
import { useAppStore } from './store/useAppStore'

function App() {
  const loadPolicies = useAppStore((s) => s.loadPolicies)
  const theme = useAppStore((s) => s.theme)
  const toggleTheme = useAppStore((s) => s.toggleTheme)

  useEffect(() => {
    void loadPolicies().catch((error: unknown) => {
      toast.error(error instanceof Error ? error.message : 'Failed to initialize policies')
    })
  }, [loadPolicies])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  return (
    <main className="h-screen w-screen p-4 text-foreground">
      <div className="flex h-full flex-col overflow-hidden rounded-xl border border-border bg-surface/70 shadow-panel backdrop-blur">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <h1 className="text-lg font-semibold text-foreground">OPA Policy Studio</h1>
            <p className="text-xs text-muted">
              Frontend-only policy CRUD + evaluation · API base: {getOpaBaseUrl()}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={toggleTheme}
              className="inline-flex items-center gap-2 rounded-md border border-border bg-panel px-3 py-1.5 text-xs text-foreground hover:border-accent"
              title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
            >
              {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
              {theme === 'dark' ? 'Light' : 'Dark'}
            </button>
            <span className="rounded-full border border-accent/40 bg-accent/10 px-3 py-1 text-xs text-accent">
              Premium UI
            </span>
          </div>
        </header>

        <section className="flex min-h-0 flex-1">
          <div className="w-[260px] flex-none">
            <PolicyExplorer />
          </div>
          <div className="w-[48%] min-w-[320px] max-w-[75vw] flex-none resize-x overflow-auto border-r border-border">
            <PolicyEditor />
          </div>
          <div className="min-w-[320px] flex-1">
            <EvaluationPanel />
          </div>
        </section>
      </div>
      <Toaster position="top-right" toastOptions={{ duration: 3000 }} />
    </main>
  )
}

export default App
