import React, { useState, useRef } from 'react'
import {
  useSessions,
  useSession,
  useAddMessage,
  useDeleteSession,
  useCommitSession
} from '../hooks'
import { format } from 'date-fns'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card'
import { Button, Input, Modal } from '../components'
import { useToast } from '../components/ui/Toast'

const SessionManagement: React.FC = () => {
  const { data: sessionsData, isLoading: isLoadingSessions, refetch: refetchSessions } = useSessions()
  const { addToast } = useToast()

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  const [showCommitModal, setShowCommitModal] = useState(false)
  const [commitWait, setCommitWait] = useState(true)
  const [messageInput, setMessageInput] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const sessions = sessionsData?.success ? sessionsData.data || [] : []
  const selectedSessionData = useSession(selectedSessionId || '')
  const addMessageMutation = useAddMessage(selectedSessionId || '')
  const deleteMutation = useDeleteSession()
  const commitMutation = useCommitSession()

  const messages = selectedSessionData.data?.success ? selectedSessionData.data.data?.messages || [] : []

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  React.useEffect(() => {
    scrollToBottom()
  }, [messages])

  const handleAddMessage = async (content: string) => {
    if (!selectedSessionId || !content.trim()) return
    try {
      await addMessageMutation.mutateAsync({
        role: 'user',
        content: content.trim()
      })
      setMessageInput('')
    } catch (err) {
      addToast({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to add message'
      })
    }
  }

  const handleDelete = async (session_id: string) => {
    if (!window.confirm('Delete this session?')) return
    try {
      await deleteMutation.mutateAsync(session_id)
      addToast({
        type: 'success',
        message: 'Session deleted successfully'
      })
      if (selectedSessionId === session_id) {
        setSelectedSessionId(null)
      }
      refetchSessions()
    } catch (err) {
      addToast({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to delete session'
      })
    }
  }

  const handleCommit = async () => {
    if (!selectedSessionId) return
    try {
      await commitMutation.mutateAsync({
        sessionId: selectedSessionId,
        wait: commitWait
      })
      setShowCommitModal(false)
      addToast({
        type: 'success',
        message: 'Session committed successfully'
      })
      refetchSessions()
    } catch (err) {
      addToast({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to commit session'
      })
    }
  }

  const formatDate = (dateString: string): string => {
    try {
      return format(new Date(dateString), 'MMM dd, yyyy HH:mm')
    } catch {
      return 'N/A'
    }
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold text-gray-800">Session Management</h1>
        <Button onClick={() => refetchSessions()}>
          Refresh
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Sessions List */}
        <Card>
          <CardHeader>
            <CardTitle>Sessions</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoadingSessions ? (
              <div className="text-center py-8 text-gray-500">Loading...</div>
            ) : sessions.length === 0 ? (
              <div className="text-center py-8 text-gray-500">No sessions</div>
            ) : (
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {sessions.map((session: any) => (
                  <div
                    key={session.id}
                    onClick={() => setSelectedSessionId(session.id)}
                    className={`p-3 border rounded cursor-pointer hover:bg-gray-50 transition-colors ${
                      selectedSessionId === session.id ? 'bg-blue-50 border-blue-200' : ''
                    }`}
                  >
                    <div className="text-sm font-medium text-gray-900">{session.id}</div>
                    <div className="text-xs text-gray-500 mt-1">
                      {formatDate(session.created_at)}
                    </div>
                    <div className="text-xs text-gray-500">
                      {session.messages?.length || 0} messages
                    </div>
                    <div className="flex items-center mt-2 space-x-2">
                      {session.compressed && (
                        <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">
                          Compressed
                        </span>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          handleDelete(session.id)
                        }}
                        className="text-xs text-red-600 hover:text-red-800"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Messages Panel */}
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>
              Messages
              {selectedSessionId && (
                <span className="text-sm font-normal text-gray-500 ml-2">
                  {selectedSessionId}
                </span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {!selectedSessionId ? (
              <div className="text-center py-8 text-gray-500">
                Select a session to view messages
              </div>
            ) : (
              <>
                <div className="space-y-4 max-h-96 overflow-y-auto mb-4">
                  {messages.length === 0 ? (
                    <div className="text-center py-8 text-gray-500">
                      No messages yet
                    </div>
                  ) : (
                    messages.map((msg: any, idx: number) => (
                      <div
                        key={`msg-${idx}-${msg.role}-${msg.content?.substring(0, 20)}`}
                        className={`p-4 rounded-lg ${
                          msg.role === 'user'
                            ? 'bg-blue-50 border border-blue-200'
                            : 'bg-gray-50 border border-gray-200'
                        }`}
                      >
                        <div className="text-sm font-medium text-gray-700 mb-1 capitalize">
                          {msg.role}
                        </div>
                        <div className="text-gray-900 whitespace-pre-wrap">
                          {msg.content}
                        </div>
                      </div>
                    ))
                  )}
                  <div ref={messagesEndRef} />
                </div>

                <div className="flex gap-2">
                  <Input
                    value={messageInput}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                      setMessageInput(e.target.value)
                    }
                    onKeyPress={(e: React.KeyboardEvent<HTMLInputElement>) =>
                      e.key === 'Enter' && handleAddMessage(messageInput)
                    }
                    placeholder="Type a message..."
                    disabled={addMessageMutation.isPending || !selectedSessionId}
                  />
                  <Button
                    onClick={() => handleAddMessage(messageInput)}
                    loading={addMessageMutation.isPending}
                    disabled={!selectedSessionId || !messageInput.trim()}
                  >
                    Send
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => setShowCommitModal(true)}
                    disabled={commitMutation.isPending || !selectedSessionId}
                  >
                    Commit
                  </Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Commit Modal */}
      <Modal
        isOpen={showCommitModal}
        onClose={() => setShowCommitModal(false)}
        title="Commit Session"
        size="small"
      >
        <div className="space-y-4">
          <p className="text-sm text-gray-600">
            Committing the session will extract memories and compress the session content.
          </p>
          <div className="flex items-center">
            <input
              type="checkbox"
              id="waitCheckbox"
              checked={commitWait}
              onChange={(e) => setCommitWait(e.target.checked)}
              className="w-4 h-4 text-blue-600"
            />
            <label htmlFor="waitCheckbox" className="ml-2 text-sm text-gray-700">
              Wait for completion
            </label>
          </div>
          <div className="flex justify-end space-x-2">
            <Button
              variant="ghost"
              onClick={() => setShowCommitModal(false)}
              disabled={commitMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={handleCommit}
              loading={commitMutation.isPending}
            >
              Commit
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}

export default SessionManagement
