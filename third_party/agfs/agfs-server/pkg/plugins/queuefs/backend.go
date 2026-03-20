package queuefs

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	log "github.com/sirupsen/logrus"
)

// QueueBackend defines the interface for queue storage backends
type QueueBackend interface {
	// Initialize initializes the backend with configuration
	Initialize(config map[string]interface{}) error

	// Close closes the backend connection
	Close() error

	// GetType returns the backend type name
	GetType() string

	// Enqueue adds a message to a queue
	Enqueue(queueName string, msg QueueMessage) error

	// Dequeue marks the first pending message as 'processing' and returns it.
	// Call Ack after successful processing to permanently delete the message.
	Dequeue(queueName string) (QueueMessage, bool, error)

	// Ack permanently deletes a message that has been successfully processed.
	Ack(queueName string, messageID string) error

	// RecoverStale resets messages stuck in 'processing' state back to 'pending'.
	// staleSec: minimum age in seconds; pass 0 to reset all processing messages.
	// Returns the number of messages recovered.
	RecoverStale(staleSec int64) (int, error)

	// Peek returns the first message without removing it
	Peek(queueName string) (QueueMessage, bool, error)

	// Size returns the number of messages in a queue
	Size(queueName string) (int, error)

	// Clear removes all messages from a queue
	Clear(queueName string) error

	// ListQueues returns all queue names (for directory listing)
	ListQueues(prefix string) ([]string, error)

	// GetLastEnqueueTime returns the timestamp of the last enqueued message
	GetLastEnqueueTime(queueName string) (time.Time, error)

	// RemoveQueue removes all messages for a queue and its nested queues
	RemoveQueue(queueName string) error

	// CreateQueue creates an empty queue (for mkdir support)
	CreateQueue(queueName string) error

	// QueueExists checks if a queue exists (even if empty)
	QueueExists(queueName string) (bool, error)
}

// MemoryBackend implements QueueBackend using in-memory storage
type MemoryBackend struct {
	queues map[string]*Queue
}

func NewMemoryBackend() *MemoryBackend {
	return &MemoryBackend{
		queues: make(map[string]*Queue),
	}
}

func (b *MemoryBackend) Initialize(config map[string]interface{}) error {
	// No initialization needed for memory backend
	return nil
}

func (b *MemoryBackend) Close() error {
	b.queues = nil
	return nil
}

func (b *MemoryBackend) GetType() string {
	return "memory"
}

func (b *MemoryBackend) getOrCreateQueue(queueName string) *Queue {
	if queue, exists := b.queues[queueName]; exists {
		return queue
	}
	queue := &Queue{
		messages:        []QueueMessage{},
		lastEnqueueTime: time.Time{},
	}
	b.queues[queueName] = queue
	return queue
}

func (b *MemoryBackend) Enqueue(queueName string, msg QueueMessage) error {
	queue := b.getOrCreateQueue(queueName)
	queue.mu.Lock()
	defer queue.mu.Unlock()

	queue.messages = append(queue.messages, msg)

	// Update lastEnqueueTime
	if msg.Timestamp.After(queue.lastEnqueueTime) {
		queue.lastEnqueueTime = msg.Timestamp
	} else {
		queue.lastEnqueueTime = queue.lastEnqueueTime.Add(1 * time.Nanosecond)
	}

	return nil
}

func (b *MemoryBackend) Dequeue(queueName string) (QueueMessage, bool, error) {
	queue, exists := b.queues[queueName]
	if !exists {
		return QueueMessage{}, false, nil
	}

	queue.mu.Lock()
	defer queue.mu.Unlock()

	if len(queue.messages) == 0 {
		return QueueMessage{}, false, nil
	}

	msg := queue.messages[0]
	queue.messages = queue.messages[1:]
	return msg, true, nil
}

// Ack is a no-op for the memory backend (messages are already removed on Dequeue).
func (b *MemoryBackend) Ack(queueName string, messageID string) error {
	return nil
}

// RecoverStale is a no-op for the memory backend (no persistence across restarts).
func (b *MemoryBackend) RecoverStale(staleSec int64) (int, error) {
	return 0, nil
}

func (b *MemoryBackend) Peek(queueName string) (QueueMessage, bool, error) {
	queue, exists := b.queues[queueName]
	if !exists {
		return QueueMessage{}, false, nil
	}

	queue.mu.Lock()
	defer queue.mu.Unlock()

	if len(queue.messages) == 0 {
		return QueueMessage{}, false, nil
	}

	return queue.messages[0], true, nil
}

func (b *MemoryBackend) Size(queueName string) (int, error) {
	queue, exists := b.queues[queueName]
	if !exists {
		return 0, nil
	}

	queue.mu.Lock()
	defer queue.mu.Unlock()

	return len(queue.messages), nil
}

func (b *MemoryBackend) Clear(queueName string) error {
	queue, exists := b.queues[queueName]
	if !exists {
		return nil
	}

	queue.mu.Lock()
	defer queue.mu.Unlock()

	queue.messages = []QueueMessage{}
	queue.lastEnqueueTime = time.Time{}
	return nil
}

func (b *MemoryBackend) ListQueues(prefix string) ([]string, error) {
	var queues []string
	for qName := range b.queues {
		if prefix == "" || qName == prefix || len(qName) > len(prefix) && qName[:len(prefix)+1] == prefix+"/" {
			queues = append(queues, qName)
		}
	}
	return queues, nil
}

func (b *MemoryBackend) GetLastEnqueueTime(queueName string) (time.Time, error) {
	queue, exists := b.queues[queueName]
	if !exists {
		return time.Time{}, nil
	}

	queue.mu.Lock()
	defer queue.mu.Unlock()

	return queue.lastEnqueueTime, nil
}

func (b *MemoryBackend) RemoveQueue(queueName string) error {
	// Remove the queue and all nested queues
	if queueName == "" {
		b.queues = make(map[string]*Queue)
		return nil
	}

	delete(b.queues, queueName)

	// Remove nested queues
	prefix := queueName + "/"
	for qName := range b.queues {
		if len(qName) > len(prefix) && qName[:len(prefix)] == prefix {
			delete(b.queues, qName)
		}
	}

	return nil
}

func (b *MemoryBackend) CreateQueue(queueName string) error {
	b.getOrCreateQueue(queueName)
	return nil
}

func (b *MemoryBackend) QueueExists(queueName string) (bool, error) {
	_, exists := b.queues[queueName]
	return exists, nil
}

// TiDBBackend implements QueueBackend using TiDB database
type TiDBBackend struct {
	db          *sql.DB
	backend     DBBackend
	backendType string
	tableCache  map[string]string // queueName -> tableName cache
	cacheMu     sync.RWMutex      // protects tableCache
}

func NewTiDBBackend() *TiDBBackend {
	return &TiDBBackend{
		tableCache: make(map[string]string),
	}
}

func (b *TiDBBackend) Initialize(config map[string]interface{}) error {
	// Store backend type from config
	backendType := "memory" // default
	if val, ok := config["backend"]; ok {
		if strVal, ok := val.(string); ok {
			backendType = strVal
		}
	}
	b.backendType = backendType

	// Create database backend
	backend, err := CreateBackend(config)
	if err != nil {
		return fmt.Errorf("failed to create backend: %w", err)
	}
	b.backend = backend

	// Open database connection
	db, err := backend.Open(config)
	if err != nil {
		return fmt.Errorf("failed to open database: %w", err)
	}
	b.db = db

	// Initialize schema
	for _, sqlStmt := range backend.GetInitSQL() {
		if _, err := db.Exec(sqlStmt); err != nil {
			db.Close()
			return fmt.Errorf("failed to initialize schema: %w", err)
		}
	}

	return nil
}

func (b *TiDBBackend) Close() error {
	if b.db != nil {
		return b.db.Close()
	}
	return nil
}

func (b *TiDBBackend) GetType() string {
	return b.backendType
}

// getTableName retrieves the table name for a queue, using cache when possible
// If forceRefresh is true, it will bypass the cache and query from database
func (b *TiDBBackend) getTableName(queueName string, forceRefresh bool) (string, error) {
	// Try to get from cache first (unless force refresh)
	if !forceRefresh {
		b.cacheMu.RLock()
		if tableName, exists := b.tableCache[queueName]; exists {
			b.cacheMu.RUnlock()
			return tableName, nil
		}
		b.cacheMu.RUnlock()
	}

	// Query from database
	var tableName string
	err := b.db.QueryRow(
		"SELECT table_name FROM queuefs_registry WHERE queue_name = ?",
		queueName,
	).Scan(&tableName)

	if err != nil {
		return "", err
	}

	// Update cache
	b.cacheMu.Lock()
	b.tableCache[queueName] = tableName
	b.cacheMu.Unlock()

	return tableName, nil
}

// invalidateCache removes a queue from the cache
func (b *TiDBBackend) invalidateCache(queueName string) {
	b.cacheMu.Lock()
	delete(b.tableCache, queueName)
	b.cacheMu.Unlock()
}

func (b *TiDBBackend) Enqueue(queueName string, msg QueueMessage) error {
	msgData, err := json.Marshal(msg)
	if err != nil {
		return fmt.Errorf("failed to marshal message: %w", err)
	}

	// Get table name from cache (lazy loading)
	tableName, err := b.getTableName(queueName, false)
	if err == sql.ErrNoRows {
		return fmt.Errorf("queue does not exist: %s (create it with mkdir first)", queueName)
	} else if err != nil {
		return fmt.Errorf("failed to get queue table name: %w", err)
	}

	// Insert message into queue table
	insertSQL := fmt.Sprintf(
		"INSERT INTO %s (message_id, data, timestamp, deleted) VALUES (?, ?, ?, 0)",
		tableName,
	)
	_, err = b.db.Exec(insertSQL, msg.ID, string(msgData), msg.Timestamp.Unix())
	if err != nil {
		return fmt.Errorf("failed to enqueue message: %w", err)
	}

	return nil
}

// Ack is not yet implemented for TiDB backend (messages are already soft-deleted on Dequeue).
func (b *TiDBBackend) Ack(queueName string, messageID string) error {
	return nil
}

// RecoverStale is not yet implemented for TiDB backend.
func (b *TiDBBackend) RecoverStale(staleSec int64) (int, error) {
	return 0, nil
}

func (b *TiDBBackend) Dequeue(queueName string) (QueueMessage, bool, error) {
	// Get table name from cache (lazy loading)
	tableName, err := b.getTableName(queueName, false)
	if err == sql.ErrNoRows {
		return QueueMessage{}, false, nil
	} else if err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to get queue table name: %w", err)
	}

	// Start transaction
	tx, err := b.db.Begin()
	if err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to start transaction: %w", err)
	}
	defer tx.Rollback()

	// Get and mark the first non-deleted message as deleted in a single atomic operation
	// Using FOR UPDATE SKIP LOCKED to skip rows locked by other transactions for better concurrency
	var id int64
	var data string

	querySQL := fmt.Sprintf(
		"SELECT id, data FROM %s WHERE deleted = 0 ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED",
		tableName,
	)
	err = tx.QueryRow(querySQL).Scan(&id, &data)

	if err == sql.ErrNoRows {
		return QueueMessage{}, false, nil
	} else if err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to query message: %w", err)
	}

	// Mark the message as deleted
	updateSQL := fmt.Sprintf(
		"UPDATE %s SET deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
		tableName,
	)
	_, err = tx.Exec(updateSQL, id)
	if err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to mark message as deleted: %w", err)
	}

	// Commit transaction
	if err := tx.Commit(); err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to commit transaction: %w", err)
	}

	// Unmarshal message
	var msg QueueMessage
	if err := json.Unmarshal([]byte(data), &msg); err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to unmarshal message: %w", err)
	}

	return msg, true, nil
}

func (b *TiDBBackend) Peek(queueName string) (QueueMessage, bool, error) {
	// Get table name from cache (lazy loading)
	tableName, err := b.getTableName(queueName, false)
	if err == sql.ErrNoRows {
		return QueueMessage{}, false, nil
	} else if err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to get queue table name: %w", err)
	}

	var data string
	querySQL := fmt.Sprintf(
		"SELECT data FROM %s WHERE deleted = 0 ORDER BY id LIMIT 1",
		tableName,
	)
	err = b.db.QueryRow(querySQL).Scan(&data)

	if err == sql.ErrNoRows {
		return QueueMessage{}, false, nil
	} else if err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to peek message: %w", err)
	}

	// Unmarshal message
	var msg QueueMessage
	if err := json.Unmarshal([]byte(data), &msg); err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to unmarshal message: %w", err)
	}

	return msg, true, nil
}

func (b *TiDBBackend) Size(queueName string) (int, error) {
	// Get table name from cache (lazy loading)
	tableName, err := b.getTableName(queueName, false)
	if err == sql.ErrNoRows {
		return 0, nil
	} else if err != nil {
		return 0, fmt.Errorf("failed to get queue table name: %w", err)
	}

	var count int
	querySQL := fmt.Sprintf(
		"SELECT COUNT(*) FROM %s WHERE deleted = 0",
		tableName,
	)
	err = b.db.QueryRow(querySQL).Scan(&count)
	if err != nil {
		return 0, fmt.Errorf("failed to get queue size: %w", err)
	}
	return count, nil
}

func (b *TiDBBackend) Clear(queueName string) error {
	// Get table name from cache (lazy loading)
	tableName, err := b.getTableName(queueName, false)
	if err == sql.ErrNoRows {
		return nil // Queue doesn't exist, nothing to clear
	} else if err != nil {
		return fmt.Errorf("failed to get queue table name: %w", err)
	}

	// Clear all messages (both deleted and non-deleted)
	deleteSQL := fmt.Sprintf("DELETE FROM %s", tableName)
	_, err = b.db.Exec(deleteSQL)
	if err != nil {
		return fmt.Errorf("failed to clear queue: %w", err)
	}
	return nil
}

func (b *TiDBBackend) ListQueues(prefix string) ([]string, error) {
	// Query from registry table to include all queues
	var query string
	var args []interface{}

	if prefix == "" {
		query = "SELECT queue_name FROM queuefs_registry"
	} else {
		query = "SELECT queue_name FROM queuefs_registry WHERE queue_name = ? OR queue_name LIKE ?"
		args = []interface{}{prefix, prefix + "/%"}
	}

	rows, err := b.db.Query(query, args...)
	if err != nil {
		return nil, fmt.Errorf("failed to list queues: %w", err)
	}
	defer rows.Close()

	var queues []string
	for rows.Next() {
		var qName string
		if err := rows.Scan(&qName); err != nil {
			return nil, fmt.Errorf("failed to scan queue name: %w", err)
		}
		queues = append(queues, qName)
	}

	return queues, nil
}

func (b *TiDBBackend) GetLastEnqueueTime(queueName string) (time.Time, error) {
	// Get table name from cache (lazy loading)
	tableName, err := b.getTableName(queueName, false)
	if err == sql.ErrNoRows {
		return time.Time{}, nil
	} else if err != nil {
		return time.Time{}, fmt.Errorf("failed to get queue table name: %w", err)
	}

	var timestamp int64
	querySQL := fmt.Sprintf(
		"SELECT MAX(timestamp) FROM %s WHERE deleted = 0",
		tableName,
	)
	err = b.db.QueryRow(querySQL).Scan(&timestamp)

	if err == sql.ErrNoRows || timestamp == 0 {
		return time.Time{}, nil
	} else if err != nil {
		return time.Time{}, fmt.Errorf("failed to get last enqueue time: %w", err)
	}

	return time.Unix(timestamp, 0), nil
}

func (b *TiDBBackend) RemoveQueue(queueName string) error {
	if queueName == "" {
		// Remove all queues: drop all queue tables and clear registry
		rows, err := b.db.Query("SELECT queue_name, table_name FROM queuefs_registry")
		if err != nil {
			return fmt.Errorf("failed to list queues: %w", err)
		}
		defer rows.Close()

		var queuesToDelete []struct {
			queueName string
			tableName string
		}

		for rows.Next() {
			var qName, tName string
			if err := rows.Scan(&qName, &tName); err != nil {
				return fmt.Errorf("failed to scan queue: %w", err)
			}
			queuesToDelete = append(queuesToDelete, struct {
				queueName string
				tableName string
			}{qName, tName})
		}

		// Drop all tables and clear cache
		for _, q := range queuesToDelete {
			dropSQL := fmt.Sprintf("DROP TABLE IF EXISTS %s", q.tableName)
			if _, err := b.db.Exec(dropSQL); err != nil {
				log.Warnf("[queuefs] Failed to drop table '%s': %v", q.tableName, err)
			}
		}

		// Clear cache completely
		b.cacheMu.Lock()
		b.tableCache = make(map[string]string)
		b.cacheMu.Unlock()

		// Clear registry
		_, err = b.db.Exec("DELETE FROM queuefs_registry")
		return err
	}

	// Remove queue and nested queues
	rows, err := b.db.Query(
		"SELECT queue_name, table_name FROM queuefs_registry WHERE queue_name = ? OR queue_name LIKE ?",
		queueName, queueName+"/%",
	)
	if err != nil {
		return fmt.Errorf("failed to query queues: %w", err)
	}
	defer rows.Close()

	var queuesToDelete []struct {
		queueName string
		tableName string
	}

	for rows.Next() {
		var qName, tName string
		if err := rows.Scan(&qName, &tName); err != nil {
			return fmt.Errorf("failed to scan queue: %w", err)
		}
		queuesToDelete = append(queuesToDelete, struct {
			queueName string
			tableName string
		}{qName, tName})
	}

	// Drop tables and invalidate cache
	for _, q := range queuesToDelete {
		dropSQL := fmt.Sprintf("DROP TABLE IF EXISTS %s", q.tableName)
		if _, err := b.db.Exec(dropSQL); err != nil {
			log.Warnf("[queuefs] Failed to drop table '%s': %v", q.tableName, err)
		} else {
			log.Infof("[queuefs] Dropped queue table '%s' for queue '%s'", q.tableName, q.queueName)
		}
		// Invalidate cache for this queue
		b.invalidateCache(q.queueName)
	}

	// Remove from registry
	_, err = b.db.Exec(
		"DELETE FROM queuefs_registry WHERE queue_name = ? OR queue_name LIKE ?",
		queueName, queueName+"/%",
	)
	return err
}

func (b *TiDBBackend) CreateQueue(queueName string) error {
	// Generate table name
	tableName := sanitizeTableName(queueName)

	// Create the queue table
	createTableSQL := getCreateTableSQL(tableName)
	if _, err := b.db.Exec(createTableSQL); err != nil {
		return fmt.Errorf("failed to create queue table: %w", err)
	}

	// Register in queuefs_registry
	_, err := b.db.Exec(
		"INSERT IGNORE INTO queuefs_registry (queue_name, table_name) VALUES (?, ?)",
		queueName, tableName,
	)
	if err != nil {
		return fmt.Errorf("failed to register queue: %w", err)
	}

	// Update cache
	b.cacheMu.Lock()
	b.tableCache[queueName] = tableName
	b.cacheMu.Unlock()

	log.Infof("[queuefs] Created queue table '%s' for queue '%s'", tableName, queueName)
	return nil
}

func (b *TiDBBackend) QueueExists(queueName string) (bool, error) {
	// Check cache first
	b.cacheMu.RLock()
	_, exists := b.tableCache[queueName]
	b.cacheMu.RUnlock()

	if exists {
		return true, nil
	}

	// If not in cache, query database
	var count int
	err := b.db.QueryRow(
		"SELECT COUNT(*) FROM queuefs_registry WHERE queue_name = ?",
		queueName,
	).Scan(&count)
	if err != nil {
		return false, fmt.Errorf("failed to check queue existence: %w", err)
	}
	return count > 0, nil
}
