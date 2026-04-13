export interface UserIdentifier {
  account_id: string
  user_id: string
  user_space_name: string
}

export interface TokenUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

/** Session list item — GET /api/v1/sessions response items. */
export interface SessionListItem {
  session_id: string
  uri: string
  is_dir: boolean
}

/** Session detail — GET /api/v1/sessions/{id} result. */
export interface SessionMeta {
  session_id: string
  created_at: string
  updated_at: string
  message_count: number
  commit_count: number
  memories_extracted: Record<string, number>
  last_commit_at: string
  llm_token_usage: TokenUsage
  embedding_token_usage: { total_tokens: number }
  pending_tokens: number
  user: UserIdentifier
}

/** POST /api/v1/sessions result. */
export interface CreateSessionResult {
  session_id: string
  user: UserIdentifier
}

/** DELETE /api/v1/sessions/{id} result. */
export interface DeleteSessionResult {
  session_id: string
}

/** POST /api/v1/sessions/{id}/messages result. */
export interface AddMessageResult {
  session_id: string
  message_count: number
}

/** POST /api/v1/sessions/{id}/commit result. */
export interface CommitSessionResult {
  session_id: string
  status: string
  task_id: string
  archive_uri: string
  archived: boolean
}
