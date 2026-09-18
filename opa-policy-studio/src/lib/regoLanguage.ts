import type * as Monaco from 'monaco-editor'

const REGO_KEYWORDS = [
  'package',
  'import',
  'default',
  'as',
  'if',
  'else',
  'not',
  'some',
  'every',
  'in',
  'with',
  'contains',
  'true',
  'false',
  'null',
]

let regoRegistered = false

export function registerRegoLanguage(monaco: typeof Monaco): void {
  if (regoRegistered) return
  regoRegistered = true

  monaco.languages.register({ id: 'rego' })

  monaco.languages.setMonarchTokensProvider('rego', {
    tokenizer: {
      root: [
        [/[a-zA-Z_][\w]*/, { cases: { '@keywords': 'keyword', '@default': 'identifier' } }],
        [/\b\d+(\.\d+)?\b/, 'number'],
        [/"/, { token: 'string.quote', bracket: '@open', next: '@string' }],
        [/#.*$/, 'comment'],
        [/[{}()[\]]/, '@brackets'],
        [/[,:]/, 'delimiter'],
        [/[.]/, 'delimiter.dot'],
        [/[:=><!+\-*/&|]+/, 'operator'],
      ],
      string: [
        [/[^\\"]+/, 'string'],
        [/\\./, 'string.escape'],
        [/"/, { token: 'string.quote', bracket: '@close', next: '@pop' }],
      ],
    },
    keywords: REGO_KEYWORDS,
  })

  monaco.languages.setLanguageConfiguration('rego', {
    comments: {
      lineComment: '#',
    },
    brackets: [
      ['{', '}'],
      ['[', ']'],
      ['(', ')'],
    ],
    autoClosingPairs: [
      { open: '{', close: '}' },
      { open: '[', close: ']' },
      { open: '(', close: ')' },
      { open: '"', close: '"' },
    ],
  })
}
