package queuefs

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	log "github.com/sirupsen/logrus"
)

// SQLiteQueueBackend implements QueueBackend using SQLite with a single-table schema.
//
// Schema:
//   - queue_metadata: tracks all queues (including empty ones created via mkdir)
//   - queue_messages: stores all messages, filtered by queue_name column
//     - status: 'pending' (waiting to be processed) | 'processing' (dequeued, awaiting ack)
//     - processing_started_at: Unix timestamp when dequeued; NULL while pending
//
// Delivery semantics: at-least-once
//   - Dequeue marks message as 'processing' (does NOT delete)
//   - Ack deletes the message after successful processing
//   - On startup, RecoverStale resets all 'processing' messages back to 'pending'
//     so that messages from a previous crashed run are automatically retried
type SQLiteQueueBackend struct {
	db *sql.DB
}

func NewSQLiteQueueBackend() *SQLiteQueueBackend {
	return &SQLiteQueueBackend{}
}

func (b *SQLiteQueueBackend) Initialize(config map[string]interface{}) error {
	dbBackend := NewSQLiteDBBackend()

	db, err := dbBackend.Open(config)
	if err != nil {
		return fmt.Errorf("failed to open SQLite database: %w", err)
	}
	b.db = db

	for _, sqlStmt := range dbBackend.GetInitSQL() {
		if _, err := db.Exec(sqlStmt); err != nil {
			db.Close()
			return fmt.Errorf("failed to initialize schema: %w", err)
		}
	}

	// Migrate existing databases: add new columns if they don't exist yet.
	b.runMigrations()

	// Reset any messages left in 'processing' state by a previous crashed process.
	// staleSec=0 resets ALL processing messages — safe at startup because no workers
	// are running yet.
	if n, err := b.RecoverStale(0); err != nil {
		log.Warnf("[queuefs] Failed to recover stale messages on startup: %v", err)
	} else if n > 0 {
		log.Infof("[queuefs] Recovered %d in-flight message(s) from previous run", n)
	}

	log.Info("[queuefs] SQLite backend initialized")
	return nil
}

// runMigrations applies schema changes needed to upgrade an existing database.
// Each ALTER TABLE is executed and "duplicate column name" errors are silently ignored.
func (b *SQLiteQueueBackend) runMigrations() {
	migrations := []string{
		`ALTER TABLE queue_messages ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'`,
		`ALTER TABLE queue_messages ADD COLUMN processing_started_at INTEGER`,
		`CREATE INDEX IF NOT EXISTS idx_queue_status ON queue_messages(queue_name, status, id)`,
		`CREATE INDEX IF NOT EXISTS idx_queue_message_id ON queue_messages(queue_name, message_id)`,
	}
	for _, stmt := range migrations {
		if _, err := b.db.Exec(stmt); err != nil {
			// "duplicate column name" means the column already exists — that's fine.
			if !strings.Contains(err.Error(), "duplicate column name") &&
				!strings.Contains(err.Error(), "already exists") {
				log.Warnf("[queuefs] Migration warning: %v", err)
			}
		}
	}
}

func (b *SQLiteQueueBackend) Close() error {
	if b.db != nil {
		return b.db.Close()
	}
	return nil
}

func (b *SQLiteQueueBackend) GetType() string {
	return "sqlite"
}

func (b *SQLiteQueueBackend) Enqueue(queueName string, msg QueueMessage) error {
	msgData, err := json.Marshal(msg)
	if err != nil {
		return fmt.Errorf("failed to marshal message: %w", err)
	}

	_, err = b.db.Exec(
		"INSERT INTO queue_messages (queue_name, message_id, data, timestamp, status) VALUES (?, ?, ?, ?, 'pending')",
		queueName, msg.ID, string(msgData), msg.Timestamp.Unix(),
	)
	if err != nil {
		return fmt.Errorf("failed to enqueue message: %w", err)
	}
	return nil
}

// Dequeue marks the first pending message as 'processing' and returns it.
// The message remains in the database until Ack is called.
// If the process crashes before Ack, RecoverStale on the next startup will
// reset the message back to 'pending' so it is retried.
func (b *SQLiteQueueBackend) Dequeue(queueName string) (QueueMessage, bool, error) {
	tx, err := b.db.Begin()
	if err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to start transaction: %w", err)
	}
	defer tx.Rollback()

	var id int64
	var data string
	err = tx.QueryRow(
		"SELECT id, data FROM queue_messages WHERE queue_name = ? AND status = 'pending' ORDER BY id LIMIT 1",
		queueName,
	).Scan(&id, &data)

	if err == sql.ErrNoRows {
		return QueueMessage{}, false, nil
	} else if err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to query message: %w", err)
	}

	// Mark as processing instead of deleting.
	_, err = tx.Exec(
		"UPDATE queue_messages SET status = 'processing', processing_started_at = ? WHERE id = ?",
		time.Now().Unix(), id,
	)
	if err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to mark message as processing: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to commit transaction: %w", err)
	}

	var msg QueueMessage
	if err := json.Unmarshal([]byte(data), &msg); err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to unmarshal message: %w", err)
	}

	return msg, true, nil
}

// Ack deletes a message that has been successfully processed.
// Should be called after the consumer has finished processing the message.
func (b *SQLiteQueueBackend) Ack(queueName string, messageID string) error {
	result, err := b.db.Exec(
		"DELETE FROM queue_messages WHERE queue_name = ? AND message_id = ? AND status = 'processing'",
		queueName, messageID,
	)
	if err != nil {
		return fmt.Errorf("failed to ack message: %w", err)
	}
	rows, _ := result.RowsAffected()
	if rows == 0 {
		log.Warnf("[queuefs] Ack found no matching processing message: queue=%s msg=%s", queueName, messageID)
	}
	return nil
}

// RecoverStale resets messages stuck in 'processing' state back to 'pending'.
// staleSec is the minimum age (in seconds) of a processing message before it
// is considered stale.  Pass 0 to reset ALL processing messages immediately
// (appropriate at startup before any workers have started).
// Returns the number of messages recovered.
func (b *SQLiteQueueBackend) RecoverStale(staleSec int64) (int, error) {
	cutoff := time.Now().Unix() - staleSec
	result, err := b.db.Exec(
		"UPDATE queue_messages SET status = 'pending', processing_started_at = NULL WHERE status = 'processing' AND processing_started_at <= ?",
		cutoff,
	)
	if err != nil {
		return 0, fmt.Errorf("failed to recover stale messages: %w", err)
	}
	n, _ := result.RowsAffected()
	return int(n), nil
}

func (b *SQLiteQueueBackend) Peek(queueName string) (QueueMessage, bool, error) {
	var data string
	err := b.db.QueryRow(
		"SELECT data FROM queue_messages WHERE queue_name = ? AND status = 'pending' ORDER BY id LIMIT 1",
		queueName,
	).Scan(&data)

	if err == sql.ErrNoRows {
		return QueueMessage{}, false, nil
	} else if err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to peek message: %w", err)
	}

	var msg QueueMessage
	if err := json.Unmarshal([]byte(data), &msg); err != nil {
		return QueueMessage{}, false, fmt.Errorf("failed to unmarshal message: %w", err)
	}

	return msg, true, nil
}

// Size returns the number of pending (not yet dequeued) messages.
func (b *SQLiteQueueBackend) Size(queueName string) (int, error) {
	var count int
	err := b.db.QueryRow(
		"SELECT COUNT(*) FROM queue_messages WHERE queue_name = ? AND status = 'pending'",
		queueName,
	).Scan(&count)
	if err != nil {
		return 0, fmt.Errorf("failed to get queue size: %w", err)
	}
	return count, nil
}

func (b *SQLiteQueueBackend) Clear(queueName string) error {
	_, err := b.db.Exec("DELETE FROM queue_messages WHERE queue_name = ?", queueName)
	if err != nil {
		return fmt.Errorf("failed to clear queue: %w", err)
	}
	return nil
}

func (b *SQLiteQueueBackend) ListQueues(prefix string) ([]string, error) {
	var query string
	var args []interface{}

	if prefix == "" {
		query = "SELECT queue_name FROM queue_metadata"
	} else {
		query = "SELECT queue_name FROM queue_metadata WHERE queue_name = ? OR queue_name LIKE ?"
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

func (b *SQLiteQueueBackend) GetLastEnqueueTime(queueName string) (time.Time, error) {
	var timestamp sql.NullInt64
	err := b.db.QueryRow(
		"SELECT MAX(timestamp) FROM queue_messages WHERE queue_name = ? AND status = 'pending'",
		queueName,
	).Scan(&timestamp)

	if err != nil || !timestamp.Valid {
		return time.Time{}, nil
	}
	return time.Unix(timestamp.Int64, 0), nil
}

func (b *SQLiteQueueBackend) RemoveQueue(queueName string) error {
	if queueName == "" {
		if _, err := b.db.Exec("DELETE FROM queue_messages"); err != nil {
			return err
		}
		_, err := b.db.Exec("DELETE FROM queue_metadata")
		return err
	}

	if _, err := b.db.Exec(
		"DELETE FROM queue_messages WHERE queue_name = ? OR queue_name LIKE ?",
		queueName, queueName+"/%",
	); err != nil {
		return fmt.Errorf("failed to remove queue messages: %w", err)
	}

	_, err := b.db.Exec(
		"DELETE FROM queue_metadata WHERE queue_name = ? OR queue_name LIKE ?",
		queueName, queueName+"/%",
	)
	return err
}

func (b *SQLiteQueueBackend) CreateQueue(queueName string) error {
	_, err := b.db.Exec(
		"INSERT OR IGNORE INTO queue_metadata (queue_name) VALUES (?)",
		queueName,
	)
	if err != nil {
		return fmt.Errorf("failed to create queue: %w", err)
	}
	log.Infof("[queuefs] Created queue '%s' (SQLite)", queueName)
	return nil
}

func (b *SQLiteQueueBackend) QueueExists(queueName string) (bool, error) {
	var count int
	err := b.db.QueryRow(
		"SELECT COUNT(*) FROM queue_metadata WHERE queue_name = ?",
		queueName,
	).Scan(&count)
	if err != nil {
		return false, fmt.Errorf("failed to check queue existence: %w", err)
	}
	return count > 0, nil
}
