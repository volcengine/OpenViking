package queuefs

import (
	"crypto/tls"
	"database/sql"
	"fmt"
	"regexp"
	"strings"

	"github.com/c4pt0r/agfs/agfs-server/pkg/plugin/config"
	"github.com/go-sql-driver/mysql"
	_ "github.com/go-sql-driver/mysql" // MySQL/TiDB driver
	_ "github.com/mattn/go-sqlite3"    // SQLite driver
	log "github.com/sirupsen/logrus"
)

// DBBackend defines the interface for database operations
type DBBackend interface {
	// Open opens a connection to the database
	Open(cfg map[string]interface{}) (*sql.DB, error)

	// GetInitSQL returns the SQL statements to initialize the schema
	GetInitSQL() []string

	// GetDriverName returns the driver name
	GetDriverName() string
}

// SQLiteDBBackend implements DBBackend for SQLite
type SQLiteDBBackend struct{}

func NewSQLiteDBBackend() *SQLiteDBBackend {
	return &SQLiteDBBackend{}
}

func (b *SQLiteDBBackend) GetDriverName() string {
	return "sqlite3"
}

func (b *SQLiteDBBackend) Open(cfg map[string]interface{}) (*sql.DB, error) {
	dbPath := config.GetStringConfig(cfg, "db_path", "queue.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return nil, fmt.Errorf("failed to open SQLite database: %w", err)
	}

	// Enable WAL mode for better concurrency
	if _, err := db.Exec("PRAGMA journal_mode=WAL"); err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to enable WAL mode: %w", err)
	}

	return db, nil
}

func (b *SQLiteDBBackend) GetInitSQL() []string {
	return []string{
		// Queue metadata table to track all queues (including empty ones)
		`CREATE TABLE IF NOT EXISTS queue_metadata (
			queue_name TEXT PRIMARY KEY,
			created_at INTEGER DEFAULT (strftime('%s', 'now')),
			last_updated INTEGER DEFAULT (strftime('%s', 'now'))
		)`,
		// Queue messages table
		// status: 'pending' (waiting) | 'processing' (dequeued, not yet acked)
		// processing_started_at: Unix timestamp when dequeued; NULL if pending
		`CREATE TABLE IF NOT EXISTS queue_messages (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			queue_name TEXT NOT NULL,
			message_id TEXT NOT NULL,
			data TEXT NOT NULL,
			timestamp INTEGER NOT NULL,
			status TEXT NOT NULL DEFAULT 'pending',
			processing_started_at INTEGER,
			created_at INTEGER DEFAULT (strftime('%s', 'now'))
		)`,
		`CREATE INDEX IF NOT EXISTS idx_queue_name ON queue_messages(queue_name)`,
		`CREATE INDEX IF NOT EXISTS idx_queue_order ON queue_messages(queue_name, id)`,
		`CREATE INDEX IF NOT EXISTS idx_queue_status ON queue_messages(queue_name, status, id)`,
		`CREATE INDEX IF NOT EXISTS idx_queue_message_id ON queue_messages(queue_name, message_id)`,
	}
}

// TiDBDBBackend implements DBBackend for TiDB
type TiDBDBBackend struct{}

func NewTiDBDBBackend() *TiDBDBBackend {
	return &TiDBDBBackend{}
}

func (b *TiDBDBBackend) GetDriverName() string {
	return "mysql"
}

func (b *TiDBDBBackend) Open(cfg map[string]interface{}) (*sql.DB, error) {
	// Check if DSN contains tls parameter
	dsnStr := config.GetStringConfig(cfg, "dsn", "")
	dsnHasTLS := strings.Contains(dsnStr, "tls=")

	// Register TLS configuration if needed
	enableTLS := config.GetBoolConfig(cfg, "enable_tls", false) || dsnHasTLS
	tlsConfigName := "tidb-queuefs"

	if enableTLS {
		// Get TLS configuration
		serverName := config.GetStringConfig(cfg, "tls_server_name", "")

		// If no explicit server name, try to extract from DSN or host
		if serverName == "" {
			if dsnStr != "" {
				// Extract host from DSN
				re := regexp.MustCompile(`@tcp\(([^:]+):\d+\)`)
				if matches := re.FindStringSubmatch(dsnStr); len(matches) > 1 {
					serverName = matches[1]
				}
			} else {
				serverName = config.GetStringConfig(cfg, "host", "")
			}
		}

		skipVerify := config.GetBoolConfig(cfg, "tls_skip_verify", false)

		tlsConfig := &tls.Config{
			MinVersion: tls.VersionTLS12,
		}

		if serverName != "" {
			tlsConfig.ServerName = serverName
		}

		if skipVerify {
			tlsConfig.InsecureSkipVerify = true
			log.Warn("[queuefs] TLS certificate verification is disabled (insecure)")
		}

		// Register TLS config
		if err := mysql.RegisterTLSConfig(tlsConfigName, tlsConfig); err != nil {
			log.Warnf("[queuefs] Failed to register TLS config (may already exist): %v", err)
		}
	}

	// Build DSN
	var dsn string

	if dsnStr != "" {
		dsn = dsnStr
	} else {
		user := config.GetStringConfig(cfg, "user", "root")
		password := config.GetStringConfig(cfg, "password", "")
		host := config.GetStringConfig(cfg, "host", "127.0.0.1")
		port := config.GetStringConfig(cfg, "port", "4000")
		database := config.GetStringConfig(cfg, "database", "queuedb")

		if password != "" {
			dsn = fmt.Sprintf("%s:%s@tcp(%s:%s)/%s?charset=utf8mb4&parseTime=True",
				user, password, host, port, database)
		} else {
			dsn = fmt.Sprintf("%s@tcp(%s:%s)/%s?charset=utf8mb4&parseTime=True",
				user, host, port, database)
		}

		if enableTLS {
			dsn += fmt.Sprintf("&tls=%s", tlsConfigName)
		}
	}

	log.Infof("[queuefs] Connecting to TiDB (TLS: %v)", enableTLS)

	// Extract database name
	dbName := extractDatabaseName(dsn, config.GetStringConfig(cfg, "database", ""))

	// Create database if needed
	if dbName != "" {
		dsnWithoutDB := removeDatabaseFromDSN(dsn)
		if dsnWithoutDB != dsn {
			tempDB, err := sql.Open("mysql", dsnWithoutDB)
			if err == nil {
				defer tempDB.Close()
				_, err = tempDB.Exec(fmt.Sprintf("CREATE DATABASE IF NOT EXISTS `%s`", dbName))
				if err != nil {
					log.Warnf("[queuefs] Failed to create database '%s': %v", dbName, err)
				} else {
					log.Infof("[queuefs] Database '%s' created or already exists", dbName)
				}
			}
		}
	}

	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, fmt.Errorf("failed to open TiDB database: %w", err)
	}

	// Set connection pool parameters
	db.SetMaxOpenConns(100)
	db.SetMaxIdleConns(10)

	// Test connection
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to ping TiDB database: %w", err)
	}

	return db, nil
}

func (b *TiDBDBBackend) GetInitSQL() []string {
	return []string{
		// Queue registry table to track all queue tables
		`CREATE TABLE IF NOT EXISTS queuefs_registry (
			queue_name VARCHAR(255) PRIMARY KEY,
			table_name VARCHAR(255) NOT NULL,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`,
	}
}

// Helper functions

func extractDatabaseName(dsn string, configDB string) string {
	if dsn != "" {
		re := regexp.MustCompile(`\)/([^?]+)`)
		if matches := re.FindStringSubmatch(dsn); len(matches) > 1 {
			return matches[1]
		}
	}
	return configDB
}

func removeDatabaseFromDSN(dsn string) string {
	re := regexp.MustCompile(`\)/[^?]+(\?|$)`)
	return re.ReplaceAllString(dsn, ")/$1")
}

// sanitizeTableName converts a queue name to a safe table name
// Replaces / with _ and ensures the name is safe for SQL
func sanitizeTableName(queueName string) string {
	// Replace forward slashes with underscores
	tableName := strings.ReplaceAll(queueName, "/", "_")

	// Replace any other potentially problematic characters
	tableName = strings.ReplaceAll(tableName, "-", "_")
	tableName = strings.ReplaceAll(tableName, ".", "_")

	// Prefix with queuefs_queue_ to avoid conflicts with system tables
	return "queuefs_queue_" + tableName
}

// getCreateTableSQL returns the SQL to create a queue table
func getCreateTableSQL(tableName string) string {
	return fmt.Sprintf(`CREATE TABLE IF NOT EXISTS %s (
		id BIGINT AUTO_INCREMENT PRIMARY KEY,
		message_id VARCHAR(64) NOT NULL,
		data LONGBLOB NOT NULL,
		timestamp BIGINT NOT NULL,
		created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
		deleted TINYINT(1) DEFAULT 0,
		deleted_at TIMESTAMP NULL,
		INDEX idx_deleted_id (deleted, id)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`, tableName)
}

// CreateBackend creates the appropriate database backend
func CreateBackend(cfg map[string]interface{}) (DBBackend, error) {
	backendType := config.GetStringConfig(cfg, "backend", "memory")

	switch backendType {
	case "sqlite", "sqlite3":
		return NewSQLiteDBBackend(), nil
	case "tidb", "mysql":
		return NewTiDBDBBackend(), nil
	default:
		return nil, fmt.Errorf("unsupported database backend: %s", backendType)
	}
}
