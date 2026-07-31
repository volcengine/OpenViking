export interface JsonFormatRequest {
  content: string
}

export interface JsonFormatResponse {
  content: string
  error: string | null
}

export function formatJsonContent(content: string): string {
  return JSON.stringify(JSON.parse(content), null, 2)
}
