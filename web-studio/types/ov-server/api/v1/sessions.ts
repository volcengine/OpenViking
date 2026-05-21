export type UserIdentifier = {
  account_id: string
  user_id: string
  user_space_name: string
}

export type TokenUsage = {
  completion_tokens: number
  prompt_tokens: number
  total_tokens: number
}

export type SessionListItem = {
  is_dir: boolean
  session_id: string
  uri: string
}

export type SessionDetail = {
  commit_count: number
  created_at: string
  embedding_token_usage: {
    total_tokens: number
  }
  last_commit_at: string
  llm_token_usage: TokenUsage
  memories_extracted: Record<string, number>
  message_count: number
  pending_tokens: number
  session_id: string
  updated_at: string
  user: UserIdentifier
}

export type SessionContextResult = {
  messages?: unknown[]
}

export type SessionCreatedResult = {
  session_id: string
  user: UserIdentifier
}

export type SessionDeletedResult = {
  session_id: string
}

export type MessageAddedResult = {
  message_count: number
  session_id: string
}

export type CommitResult = {
  archive_uri: string
  archived: boolean
  session_id: string
  status: string
  task_id: string
}

export type SessionMeta = SessionDetail
export type CreateSessionResult = SessionCreatedResult
export type DeleteSessionResult = SessionDeletedResult
export type AddMessageResult = MessageAddedResult
export type CommitSessionResult = CommitResult
