import axios from 'axios'
import type { Policy } from '../types'

const isDev = import.meta.env.DEV
const configuredBaseUrl = import.meta.env.VITE_OPA_BASE_URL
const useDirectInDev = import.meta.env.VITE_OPA_USE_DIRECT === 'true'
const opaBaseUrl = isDev
  ? useDirectInDev
    ? configuredBaseUrl ?? 'http://localhost:8080'
    : '/opa'
  : configuredBaseUrl ?? 'http://localhost:8080'

export function getOpaBaseUrl(): string {
  return opaBaseUrl
}

function encodePolicyId(id: string): string {
  return id
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/')
}

type OpaPolicyListResponse = {
  result?: Array<{
    id: string
    raw?: string
    ast?: {
      package?: {
        path?: Array<{ value?: string }>
      }
    }
  }>
}

type OpaPolicyGetResponse = {
  result?: {
    id: string
    raw?: string
    ast?: {
      package?: {
        path?: Array<{ value?: string }>
      }
    }
  }
}

type OpaErrorResponse = {
  code?: string
  message?: string
  errors?: Array<{ message?: string }>
}

export const opaApi = axios.create({
  baseURL: opaBaseUrl,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const payload = error.response?.data as OpaErrorResponse | undefined
    return (
      payload?.message ??
      payload?.errors?.[0]?.message ??
      error.message ??
      'Unexpected OPA error'
    )
  }
  return error instanceof Error ? error.message : 'Unexpected OPA error'
}

export function toUserError(prefix: string, error: unknown): Error {
  return new Error(`${prefix}: ${extractErrorMessage(error)}`)
}

function parseDataPathFromAst(pathNodes?: Array<{ value?: string }>): string | undefined {
  if (!pathNodes || pathNodes.length === 0) return undefined
  const segments = pathNodes
    .map((node) => node.value?.trim())
    .filter((value): value is string => Boolean(value))
  if (segments.length === 0) return undefined
  const withoutDataPrefix = segments[0] === 'data' ? segments.slice(1) : segments
  if (withoutDataPrefix.length === 0) return undefined
  return withoutDataPrefix.join('/')
}

function parseDataPathFromRaw(raw?: string): string | undefined {
  if (!raw) return undefined
  const packageMatch = raw.match(/^\s*package\s+([a-zA-Z0-9_.-]+)/m)
  if (!packageMatch?.[1]) return undefined
  return packageMatch[1].split('.').join('/')
}

function derivePolicyDataPath(
  astPath?: Array<{ value?: string }>,
  raw?: string,
): string | undefined {
  return parseDataPathFromAst(astPath) ?? parseDataPathFromRaw(raw)
}

export async function listPolicies(): Promise<Policy[]> {
  const response = await opaApi.get<OpaPolicyListResponse>('/v1/policies')
  const items = response.data.result ?? []
  return items.map((item) => ({
    id: item.id,
    raw: item.raw ?? '',
    dataPath: derivePolicyDataPath(item.ast?.package?.path, item.raw),
  }))
}

export async function getPolicy(id: string): Promise<Policy> {
  const response = await opaApi.get<OpaPolicyGetResponse>(`/v1/policies/${encodePolicyId(id)}`)
  const result = response.data.result
  if (!result) {
    throw new Error(`Policy "${id}" not found`)
  }
  return {
    id: result.id,
    raw: result.raw ?? '',
    dataPath: derivePolicyDataPath(result.ast?.package?.path, result.raw),
  }
}

export async function upsertPolicy(id: string, content: string): Promise<void> {
  await opaApi.put(`/v1/policies/${encodePolicyId(id)}`, content, {
    headers: { 'Content-Type': 'text/plain' },
  })
}

export async function deletePolicy(id: string): Promise<void> {
  await opaApi.delete(`/v1/policies/${encodePolicyId(id)}`)
}

export async function evaluatePolicy(path: string, input: unknown): Promise<unknown> {
  const normalizedPath = path
    .trim()
    .replace(/^https?:\/\/[^/]+\/?/, '')
    .replace(/^\/+/, '')
    .replace(/^v1\/data\/?/i, '')
    .replace(/^data\/?/i, '')
    .split('/')
    .filter((segment) => segment.length > 0)
    .map((segment) => encodeURIComponent(segment))
    .join('/')
  const response = await opaApi.post(`/v1/data/${normalizedPath}`, { input })
  return response.data
}
