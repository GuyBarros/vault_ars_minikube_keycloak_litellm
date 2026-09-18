import { create } from 'zustand'
import type { Policy } from '../types'
import {
  deletePolicy,
  evaluatePolicy,
  getOpaBaseUrl,
  getPolicy,
  listPolicies,
  toUserError,
  upsertPolicy,
} from '../lib/opaClient'

const defaultPolicy = `package app.security

default allow := false

allow if {
  input.user == "admin"
}
`

const defaultEvaluationInput = `{
  "user": "admin"
}`

export type AppTheme = 'dark' | 'light'

function getInitialTheme(): AppTheme {
  if (typeof window === 'undefined') {
    return 'dark'
  }
  const storedTheme = window.localStorage.getItem('policy-studio-theme')
  if (storedTheme === 'light' || storedTheme === 'dark') {
    return storedTheme
  }
  return 'dark'
}

function encodeUtf8Base64(value: string): string {
  const bytes = new TextEncoder().encode(value)
  let binary = ''
  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
  }
  return btoa(binary)
}

function normalizeEvaluationInput(input: string): string {
  const trimmed = input.trim()
  if (!trimmed) {
    return ''
  }
  try {
    const parsed = JSON.parse(trimmed) as unknown
    return JSON.stringify(parsed)
  } catch {
    return input
  }
}

function formatEvaluationResult(result: unknown): string {
  if (typeof result === 'string') {
    return result
  }
  if (result === undefined) {
    return '{\n  "result": null\n}'
  }
  return JSON.stringify(result, null, 2)
}

type AppState = {
  theme: AppTheme
  policies: Policy[]
  selectedPolicyId: string | null
  policyContent: string
  evaluationPath: string
  evaluationInput: string
  evaluationResult: string
  loadingPolicies: boolean
  savingPolicy: boolean
  evaluatingPolicy: boolean
  lastPolicyLoadError: string | null
  loadPolicies: () => Promise<void>
  selectPolicy: (id: string) => Promise<void>
  setPolicyContent: (content: string) => void
  setEvaluationInput: (content: string) => void
  setEvaluationPath: (path: string) => void
  createPolicy: (id: string) => Promise<void>
  savePolicy: () => Promise<void>
  removePolicy: (id: string) => Promise<void>
  runEvaluation: () => Promise<void>
  setTheme: (theme: AppTheme) => void
  toggleTheme: () => void
}

export const useAppStore = create<AppState>((set, get) => ({
  theme: getInitialTheme(),
  policies: [],
  selectedPolicyId: null,
  policyContent: defaultPolicy,
  evaluationPath: 'app/security',
  evaluationInput: defaultEvaluationInput,
  evaluationResult: '{\n  "result": "Run evaluation to see output"\n}',
  loadingPolicies: false,
  savingPolicy: false,
  evaluatingPolicy: false,
  lastPolicyLoadError: null,
  loadPolicies: async () => {
    set({ loadingPolicies: true, lastPolicyLoadError: null })
    try {
      const policies = await listPolicies()
      const selectedPolicyId = get().selectedPolicyId
      const hasSelected = selectedPolicyId && policies.some((p) => p.id === selectedPolicyId)
      set({
        policies,
        selectedPolicyId: hasSelected ? selectedPolicyId : policies[0]?.id ?? null,
      })
      if (!hasSelected && policies[0]) {
        const policy = await getPolicy(policies[0].id)
        set({
          policyContent: policy.raw,
          evaluationPath: policy.dataPath ?? get().evaluationPath,
        })
      }
    } catch (error) {
      const message = toUserError('Failed to load policies', error).message
      const diagnostic = `${message}. Check OPA at ${getOpaBaseUrl()} and CORS settings.`
      set({ lastPolicyLoadError: diagnostic })
      throw new Error(diagnostic)
    } finally {
      set({ loadingPolicies: false })
    }
  },
  selectPolicy: async (id: string) => {
    try {
      const policy = await getPolicy(id)
      set({
        selectedPolicyId: id,
        policyContent: policy.raw,
        evaluationPath: policy.dataPath ?? get().evaluationPath,
      })
    } catch (error) {
      throw toUserError(`Failed to load policy "${id}"`, error)
    }
  },
  setPolicyContent: (content: string) => set({ policyContent: content }),
  setEvaluationInput: (content: string) => set({ evaluationInput: content }),
  setEvaluationPath: (path: string) => set({ evaluationPath: path }),
  createPolicy: async (id: string) => {
    try {
      await upsertPolicy(id, defaultPolicy)
      await get().loadPolicies()
      await get().selectPolicy(id)
    } catch (error) {
      throw toUserError(`Failed to create policy "${id}"`, error)
    }
  },
  savePolicy: async () => {
    const { selectedPolicyId, policyContent } = get()
    if (!selectedPolicyId) {
      throw new Error('Select a policy before saving')
    }
    set({ savingPolicy: true })
    try {
      await upsertPolicy(selectedPolicyId, policyContent)
      await get().loadPolicies()
    } catch (error) {
      throw toUserError(`Failed to save policy "${selectedPolicyId}"`, error)
    } finally {
      set({ savingPolicy: false })
    }
  },
  removePolicy: async (id: string) => {
    try {
      await deletePolicy(id)
      await get().loadPolicies()
    } catch (error) {
      throw toUserError(`Failed to delete policy "${id}"`, error)
    }
  },
  runEvaluation: async () => {
    const { evaluationInput, evaluationPath } = get()
    set({ evaluatingPolicy: true })
    try {
      const normalizedInput = normalizeEvaluationInput(evaluationInput)
      const encodedInput = encodeUtf8Base64(normalizedInput)
      const result = await evaluatePolicy(evaluationPath, encodedInput)
      set({
        evaluationResult: formatEvaluationResult(result),
      })
    } catch (error) {
      throw toUserError('Failed to evaluate policy', error)
    } finally {
      set({ evaluatingPolicy: false })
    }
  },
  setTheme: (theme: AppTheme) => {
    window.localStorage.setItem('policy-studio-theme', theme)
    set({ theme })
  },
  toggleTheme: () => {
    const nextTheme: AppTheme = get().theme === 'dark' ? 'light' : 'dark'
    window.localStorage.setItem('policy-studio-theme', nextTheme)
    set({ theme: nextTheme })
  },
}))
