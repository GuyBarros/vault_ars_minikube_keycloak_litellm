export type Policy = {
  id: string
  raw: string
  dataPath?: string
}

export type OpaError = {
  error: string
  details?: string
}
