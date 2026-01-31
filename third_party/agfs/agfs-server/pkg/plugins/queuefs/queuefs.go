package queuefs

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"path"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/c4pt0r/agfs/agfs-server/pkg/filesystem"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugin"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugin/config"
	"github.com/google/uuid"
	log "github.com/sirupsen/logrus"
)

const (
	PluginName = "queuefs" // Name of this plugin
)

// Meta values for QueueFS plugin
const (
	MetaValueQueueControl = "control" // Queue control files (enqueue, dequeue, peek, clear)
	MetaValueQueueStatus  = "status"  // Queue status files (size)
)

// QueueFSPlugin provides a message queue service through a file system interface.
// Each queue is a directory containing control files:
//
//	/queue_name/enqueue - write to this file to enqueue a message
//	/queue_name/dequeue - read from this file to dequeue a message
//	/queue_name/peek    - read to peek at the next message without removing it
//	                      The peek file's modTime reflects the latest enqueued message timestamp
//	                      This can be used for implementing poll offset logic
//	/queue_name/size    - read to get queue size
//	/queue_name/clear   - write to this file to clear the queue
//
// Supports multiple backends:
//   - memory (default): In-memory storage
//   - tidb: TiDB database storage with TLS support
//   - sqlite: SQLite database storage
type QueueFSPlugin struct {
	backend  QueueBackend
	mu       sync.RWMutex // Protects backend operations
	metadata plugin.PluginMetadata
}

// Queue represents a single message queue (for memory backend)
type Queue struct {
	messages        []QueueMessage
	mu              sync.Mutex
	lastEnqueueTime time.Time // Tracks the timestamp of the most recently enqueued message
}

type QueueMessage struct {
	ID        string    `json:"id"`
	Data      string    `json:"data"`
	Timestamp time.Time `json:"timestamp"`
}

// NewQueueFSPlugin creates a new queue plugin
func NewQueueFSPlugin() *QueueFSPlugin {
	return &QueueFSPlugin{
		metadata: plugin.PluginMetadata{
			Name:        PluginName,
			Version:     "1.0.0",
			Description: "Message queue service plugin with multiple queue support and pluggable backends",
			Author:      "AGFS Server",
		},
	}
}

func (q *QueueFSPlugin) Name() string {
	return q.metadata.Name
}

func (q *QueueFSPlugin) Validate(cfg map[string]interface{}) error {
	// Allowed configuration keys
	allowedKeys := []string{
		"backend", "mount_path",
		// Database-related keys
		"db_path", "dsn", "user", "password", "host", "port", "database",
		"enable_tls", "tls_server_name", "tls_skip_verify",
	}
	if err := config.ValidateOnlyKnownKeys(cfg, allowedKeys); err != nil {
		return err
	}

	// Validate backend type
	backendType := config.GetStringConfig(cfg, "backend", "memory")
	validBackends := map[string]bool{
		"memory":  true,
		"tidb":    true,
		"mysql":   true,
		"sqlite":  true,
		"sqlite3": true,
	}
	if !validBackends[backendType] {
		return fmt.Errorf("unsupported backend: %s (valid options: memory, tidb, mysql, sqlite)", backendType)
	}

	// Validate database-related parameters if backend is not memory
	if backendType != "memory" {
		for _, key := range []string{"db_path", "dsn", "user", "password", "host", "database", "tls_server_name"} {
			if err := config.ValidateStringType(cfg, key); err != nil {
				return err
			}
		}

		for _, key := range []string{"port"} {
			if err := config.ValidateIntType(cfg, key); err != nil {
				return err
			}
		}

		for _, key := range []string{"enable_tls", "tls_skip_verify"} {
			if err := config.ValidateBoolType(cfg, key); err != nil {
				return err
			}
		}
	}

	return nil
}

func (q *QueueFSPlugin) Initialize(cfg map[string]interface{}) error {
	backendType := config.GetStringConfig(cfg, "backend", "memory")

	// Create appropriate backend
	var backend QueueBackend
	var err error

	switch backendType {
	case "memory":
		backend = NewMemoryBackend()
	case "tidb", "mysql", "sqlite", "sqlite3":
		backend = NewTiDBBackend()
	default:
		return fmt.Errorf("unsupported backend: %s", backendType)
	}

	// Initialize backend
	if err = backend.Initialize(cfg); err != nil {
		return fmt.Errorf("failed to initialize %s backend: %w", backendType, err)
	}

	q.backend = backend

	log.Infof("[queuefs] Initialized with backend: %s", backendType)
	return nil
}

func (q *QueueFSPlugin) GetFileSystem() filesystem.FileSystem {
	return &queueFS{plugin: q}
}

func (q *QueueFSPlugin) GetReadme() string {
	return `QueueFS Plugin - Multiple Message Queue Service

This plugin provides multiple message queue services through a file system interface.
Each queue is a directory containing control files for queue operations.

STRUCTURE:
  /queuefs/
    README          - This documentation
    <queue_name>/   - A queue directory
      enqueue       - Write-only file to enqueue messages
      dequeue       - Read-only file to dequeue messages
      peek          - Read-only file to peek at next message
      size          - Read-only file showing queue size
      clear         - Write-only file to clear all messages

WORKFLOW:
  1. Create a queue:
     mkdir /queuefs/my_queue

  2. Enqueue messages:
     echo "your message" > /queuefs/my_queue/enqueue

  3. Dequeue messages:
     cat /queuefs/my_queue/dequeue

  4. Check queue size:
     cat /queuefs/my_queue/size

  5. Peek without removing:
     cat /queuefs/my_queue/peek

  6. Clear the queue:
     echo "" > /queuefs/my_queue/clear

  7. Delete the queue:
     rm -rf /queuefs/my_queue

NESTED QUEUES:
  You can create queues in nested directories:
    mkdir -p /queuefs/logs/errors
    echo "error: timeout" > /queuefs/logs/errors/enqueue
    cat /queuefs/logs/errors/dequeue

BACKENDS:

  Memory Backend (default):
  [plugins.queuefs]
  enabled = true
  path = "/queuefs"
  # No additional config needed for memory backend

  SQLite Backend:
  [plugins.queuefs]
  enabled = true
  path = "/queuefs"

    [plugins.queuefs.config]
    backend = "sqlite"
    db_path = "queue.db"

  TiDB Backend (local):
  [plugins.queuefs]
  enabled = true
  path = "/queuefs"

    [plugins.queuefs.config]
    backend = "tidb"
    host = "127.0.0.1"
    port = "4000"
    user = "root"
    password = ""
    database = "queuedb"

  TiDB Cloud Backend (with TLS):
  [plugins.queuefs]
  enabled = true
  path = "/queuefs"

    [plugins.queuefs.config]
    backend = "tidb"
    user = "3YdGXuXNdAEmP1f.root"
    password = "your_password"
    host = "gateway01.us-west-2.prod.aws.tidbcloud.com"
    port = "4000"
    database = "queuedb"
    enable_tls = true
    tls_server_name = "gateway01.us-west-2.prod.aws.tidbcloud.com"

EXAMPLES:
  # Create multiple queues
  agfs:/> mkdir /queuefs/orders
  agfs:/> mkdir /queuefs/notifications
  agfs:/> mkdir /queuefs/logs/errors

  # Enqueue messages to different queues
  agfs:/> echo "order-123" > /queuefs/orders/enqueue
  agfs:/> echo "user login" > /queuefs/notifications/enqueue
  agfs:/> echo "connection timeout" > /queuefs/logs/errors/enqueue

  # Check queue sizes
  agfs:/> cat /queuefs/orders/size
  1

  # Dequeue messages
  agfs:/> cat /queuefs/orders/dequeue
  {"id":"...","data":"order-123","timestamp":"..."}

  # List all queues
  agfs:/> ls /queuefs/
  README  orders  notifications  logs

  # Delete a queue when done
  agfs:/> rm -rf /queuefs/orders

BACKEND COMPARISON:
  - memory: Fastest, no persistence, lost on restart
  - sqlite: Good for single server, persistent, file-based
  - tidb: Best for production, distributed, scalable, persistent
`
}

func (q *QueueFSPlugin) GetConfigParams() []plugin.ConfigParameter {
	return []plugin.ConfigParameter{
		{
			Name:        "backend",
			Type:        "string",
			Required:    false,
			Default:     "memory",
			Description: "Queue backend (memory, tidb, mysql, sqlite, sqlite3)",
		},
		{
			Name:        "db_path",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "Database file path (for SQLite)",
		},
		{
			Name:        "dsn",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "Database connection string (DSN)",
		},
		{
			Name:        "user",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "Database username",
		},
		{
			Name:        "password",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "Database password",
		},
		{
			Name:        "host",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "Database host",
		},
		{
			Name:        "port",
			Type:        "int",
			Required:    false,
			Default:     "",
			Description: "Database port",
		},
		{
			Name:        "database",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "Database name",
		},
		{
			Name:        "enable_tls",
			Type:        "bool",
			Required:    false,
			Default:     "false",
			Description: "Enable TLS for database connection",
		},
		{
			Name:        "tls_server_name",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "TLS server name for verification",
		},
		{
			Name:        "tls_skip_verify",
			Type:        "bool",
			Required:    false,
			Default:     "false",
			Description: "Skip TLS certificate verification",
		},
	}
}

func (q *QueueFSPlugin) Shutdown() error {
	q.mu.Lock()
	defer q.mu.Unlock()

	if q.backend != nil {
		return q.backend.Close()
	}
	return nil
}

// queueFS implements the FileSystem interface for queue operations
type queueFS struct {
	plugin *QueueFSPlugin
}

// Control file operations supported within each queue directory
var queueOperations = map[string]bool{
	"enqueue": true,
	"dequeue": true,
	"peek":    true,
	"size":    true,
	"clear":   true,
}

// parseQueuePath parses a path like "/queue_name/operation" or "/dir/queue_name/operation"
// Returns (queueName, operation, isDir, error)
func parseQueuePath(p string) (queueName string, operation string, isDir bool, err error) {
	// Clean the path
	p = path.Clean(p)

	if p == "/" || p == "." {
		return "", "", true, nil
	}

	// Remove leading slash
	p = strings.TrimPrefix(p, "/")

	// Split path into components
	parts := strings.Split(p, "/")

	if len(parts) == 0 {
		return "", "", true, nil
	}

	// Check if the last component is a queue operation
	lastPart := parts[len(parts)-1]
	if queueOperations[lastPart] {
		// This is a queue operation file
		if len(parts) == 1 {
			return "", "", false, fmt.Errorf("invalid path: operation without queue name")
		}
		queueName = strings.Join(parts[:len(parts)-1], "/")
		operation = lastPart
		return queueName, operation, false, nil
	}

	// This is a queue directory (or parent directory)
	queueName = strings.Join(parts, "/")
	return queueName, "", true, nil
}

// isValidQueueOperation checks if an operation name is valid
func isValidQueueOperation(op string) bool {
	return queueOperations[op]
}

func (qfs *queueFS) Create(path string) error {
	_, operation, isDir, err := parseQueuePath(path)
	if err != nil {
		return err
	}

	if isDir {
		return fmt.Errorf("cannot create files: %s is a directory", path)
	}

	if operation != "" && isValidQueueOperation(operation) {
		// Control files are virtual, no need to create
		return nil
	}

	return fmt.Errorf("cannot create files in queuefs: %s", path)
}

func (qfs *queueFS) Mkdir(path string, perm uint32) error {
	queueName, _, isDir, err := parseQueuePath(path)
	if err != nil {
		return err
	}

	if !isDir {
		return fmt.Errorf("cannot create directory: %s is not a valid directory path", path)
	}

	if queueName == "" {
		return fmt.Errorf("invalid queue name")
	}

	// Create queue in backend
	qfs.plugin.mu.Lock()
	defer qfs.plugin.mu.Unlock()

	return qfs.plugin.backend.CreateQueue(queueName)
}

func (qfs *queueFS) Remove(path string) error {
	_, operation, isDir, err := parseQueuePath(path)
	if err != nil {
		return err
	}

	if isDir {
		return fmt.Errorf("cannot remove directory with Remove: use RemoveAll instead")
	}

	if operation != "" {
		return fmt.Errorf("cannot remove control files: %s", path)
	}

	return fmt.Errorf("cannot remove: %s", path)
}

func (qfs *queueFS) RemoveAll(path string) error {
	queueName, _, isDir, err := parseQueuePath(path)
	if err != nil {
		return err
	}

	if !isDir {
		return fmt.Errorf("cannot remove: %s is not a directory", path)
	}

	qfs.plugin.mu.Lock()
	defer qfs.plugin.mu.Unlock()

	return qfs.plugin.backend.RemoveQueue(queueName)
}

func (qfs *queueFS) Read(path string, offset int64, size int64) ([]byte, error) {
	// Special case: README at root
	if path == "/README" {
		data := []byte(qfs.plugin.GetReadme())
		return plugin.ApplyRangeRead(data, offset, size)
	}

	queueName, operation, isDir, err := parseQueuePath(path)
	if err != nil {
		return nil, err
	}

	if isDir {
		return nil, fmt.Errorf("is a directory: %s", path)
	}

	if operation == "" {
		return nil, fmt.Errorf("no such file: %s", path)
	}

	var data []byte

	switch operation {
	case "dequeue":
		data, err = qfs.dequeue(queueName)
	case "peek":
		data, err = qfs.peek(queueName)
	case "size":
		data, err = qfs.size(queueName)
	case "enqueue", "clear":
		// Write-only files
		return []byte(""), fmt.Errorf("permission denied: %s is write-only", path)
	default:
		return nil, fmt.Errorf("no such file: %s", path)
	}

	if err != nil {
		return nil, err
	}

	return plugin.ApplyRangeRead(data, offset, size)
}

func (qfs *queueFS) Write(path string, data []byte, offset int64, flags filesystem.WriteFlag) (int64, error) {
	queueName, operation, isDir, err := parseQueuePath(path)
	if err != nil {
		return 0, err
	}

	if isDir {
		return 0, fmt.Errorf("is a directory: %s", path)
	}

	if operation == "" {
		return 0, fmt.Errorf("cannot write to: %s", path)
	}

	// QueueFS is append-only for enqueue, offset is ignored
	switch operation {
	case "enqueue":
		// TODO: ignore the enqueue content to fit the FS interface
		_, err := qfs.enqueue(queueName, data)
		if err != nil {
			return 0, err
		}
		// Note: msgID is no longer returned via Write return value
		// Clients should use other mechanisms (e.g., response headers) if needed
		return int64(len(data)), nil
	case "clear":
		if err := qfs.clear(queueName); err != nil {
			return 0, err
		}
		return 0, nil
	default:
		return 0, fmt.Errorf("cannot write to: %s", path)
	}
}

func (qfs *queueFS) ReadDir(path string) ([]filesystem.FileInfo, error) {
	queueName, _, isDir, err := parseQueuePath(path)
	if err != nil {
		return nil, err
	}

	if !isDir {
		return nil, fmt.Errorf("not a directory: %s", path)
	}

	now := time.Now()

	// Root directory: list all queues + README
	if path == "/" || queueName == "" {
		qfs.plugin.mu.RLock()
		defer qfs.plugin.mu.RUnlock()

		readme := qfs.plugin.GetReadme()
		files := []filesystem.FileInfo{
			{
				Name:    "README",
				Size:    int64(len(readme)),
				Mode:    0444, // read-only
				ModTime: now,
				IsDir:   false,
				Meta:    filesystem.MetaData{Name: PluginName, Type: "doc"},
			},
		}

		// Get all queues from backend
		queues, err := qfs.plugin.backend.ListQueues("")
		if err != nil {
			return nil, err
		}

		// Extract top-level directories
		topLevelDirs := make(map[string]bool)
		for _, qName := range queues {
			parts := strings.Split(qName, "/")
			if len(parts) > 0 {
				topLevelDirs[parts[0]] = true
			}
		}

		for dirName := range topLevelDirs {
			files = append(files, filesystem.FileInfo{
				Name:    dirName,
				Size:    0,
				Mode:    0755,
				ModTime: now,
				IsDir:   true,
				Meta:    filesystem.MetaData{Name: PluginName, Type: "queue"},
			})
		}

		return files, nil
	}

	// Check if this is an actual queue or intermediate directory
	qfs.plugin.mu.RLock()
	defer qfs.plugin.mu.RUnlock()

	// Check if queue has messages
	size, err := qfs.plugin.backend.Size(queueName)
	if err != nil {
		return nil, err
	}

	if size > 0 {
		// This is an actual queue with messages - return control files
		return qfs.getQueueControlFiles(queueName, now)
	}

	// Check for nested queues
	queues, err := qfs.plugin.backend.ListQueues(queueName)
	if err != nil {
		return nil, err
	}

	subdirs := make(map[string]bool)
	hasNested := false

	for _, qName := range queues {
		if qName == queueName {
			continue
		}
		if strings.HasPrefix(qName, queueName+"/") {
			hasNested = true
			remainder := strings.TrimPrefix(qName, queueName+"/")
			parts := strings.Split(remainder, "/")
			if len(parts) > 0 {
				subdirs[parts[0]] = true
			}
		}
	}

	if !hasNested {
		// No messages and no nested queues - treat as empty queue directory
		return qfs.getQueueControlFiles(queueName, now)
	}

	// Return subdirectories
	var files []filesystem.FileInfo
	for subdir := range subdirs {
		files = append(files, filesystem.FileInfo{
			Name:    subdir,
			Size:    0,
			Mode:    0755,
			ModTime: now,
			IsDir:   true,
			Meta:    filesystem.MetaData{Name: PluginName, Type: "queue"},
		})
	}

	return files, nil
}

func (qfs *queueFS) getQueueControlFiles(queueName string, now time.Time) ([]filesystem.FileInfo, error) {
	// Get queue size
	queueSize, err := qfs.plugin.backend.Size(queueName)
	if err != nil {
		queueSize = 0
	}

	// Get last enqueue time for peek ModTime
	lastEnqueueTime, err := qfs.plugin.backend.GetLastEnqueueTime(queueName)
	if err != nil || lastEnqueueTime.IsZero() {
		lastEnqueueTime = now
	}

	files := []filesystem.FileInfo{
		{
			Name:    "enqueue",
			Size:    0,
			Mode:    0222, // write-only
			ModTime: now,
			IsDir:   false,
			Meta:    filesystem.MetaData{Name: PluginName, Type: MetaValueQueueControl},
		},
		{
			Name:    "dequeue",
			Size:    0,
			Mode:    0444, // read-only
			ModTime: now,
			IsDir:   false,
			Meta:    filesystem.MetaData{Name: PluginName, Type: MetaValueQueueControl},
		},
		{
			Name:    "peek",
			Size:    0,
			Mode:    0444,            // read-only
			ModTime: lastEnqueueTime, // Use last enqueue time for poll offset tracking
			IsDir:   false,
			Meta:    filesystem.MetaData{Name: PluginName, Type: MetaValueQueueControl},
		},
		{
			Name:    "size",
			Size:    int64(len(strconv.Itoa(queueSize))),
			Mode:    0444, // read-only
			ModTime: now,
			IsDir:   false,
			Meta:    filesystem.MetaData{Name: PluginName, Type: MetaValueQueueStatus},
		},
		{
			Name:    "clear",
			Size:    0,
			Mode:    0222, // write-only
			ModTime: now,
			IsDir:   false,
			Meta:    filesystem.MetaData{Name: PluginName, Type: MetaValueQueueControl},
		},
	}

	return files, nil
}

func (qfs *queueFS) Stat(p string) (*filesystem.FileInfo, error) {
	if p == "/" {
		return &filesystem.FileInfo{
			Name:    "/",
			Size:    0,
			Mode:    0755,
			ModTime: time.Now(),
			IsDir:   true,
			Meta: filesystem.MetaData{
				Name: PluginName,
				Content: map[string]string{
					"backend": qfs.plugin.backend.GetType(),
				},
			},
		}, nil
	}

	// Special case: README at root
	if p == "/README" {
		readme := qfs.plugin.GetReadme()
		return &filesystem.FileInfo{
			Name:    "README",
			Size:    int64(len(readme)),
			Mode:    0444,
			ModTime: time.Now(),
			IsDir:   false,
			Meta:    filesystem.MetaData{Name: PluginName, Type: "doc"},
		}, nil
	}

	queueName, operation, isDir, err := parseQueuePath(p)
	if err != nil {
		return nil, err
	}

	now := time.Now()

	// Directory stat
	if isDir {
		name := path.Base(p)
		if name == "." || name == "/" {
			name = "/"
		}

		// Check if queue exists
		qfs.plugin.mu.RLock()
		exists, err := qfs.plugin.backend.QueueExists(queueName)
		if err != nil {
			qfs.plugin.mu.RUnlock()
			return nil, fmt.Errorf("failed to check queue existence: %w", err)
		}

		// If queue doesn't exist, check if it's a parent directory of existing queues
		if !exists {
			queues, err := qfs.plugin.backend.ListQueues(queueName)
			if err != nil {
				qfs.plugin.mu.RUnlock()
				return nil, fmt.Errorf("failed to list queues: %w", err)
			}
			// Check if any queue starts with this path as a prefix
			hasChildren := false
			for _, q := range queues {
				if strings.HasPrefix(q, queueName+"/") {
					hasChildren = true
					break
				}
			}
			if !hasChildren {
				qfs.plugin.mu.RUnlock()
				return nil, fmt.Errorf("no such file or directory: %s", p)
			}
		}
		qfs.plugin.mu.RUnlock()

		return &filesystem.FileInfo{
			Name:    name,
			Size:    0,
			Mode:    0755,
			ModTime: now,
			IsDir:   true,
			Meta:    filesystem.MetaData{Name: PluginName, Type: "queue"},
		}, nil
	}

	// Control file stat
	if operation == "" {
		return nil, fmt.Errorf("no such file: %s", p)
	}

	mode := uint32(0644)
	if operation == "enqueue" || operation == "clear" {
		mode = 0222
	} else {
		mode = 0444
	}

	fileType := MetaValueQueueControl
	size := int64(0)
	modTime := now

	if operation == "size" {
		fileType = MetaValueQueueStatus
		queueSize, _ := qfs.plugin.backend.Size(queueName)
		size = int64(len(strconv.Itoa(queueSize)))
	} else if operation == "peek" {
		// Use last enqueue time for peek's ModTime
		lastEnqueueTime, err := qfs.plugin.backend.GetLastEnqueueTime(queueName)
		if err == nil && !lastEnqueueTime.IsZero() {
			modTime = lastEnqueueTime
		}
	}

	return &filesystem.FileInfo{
		Name:    operation,
		Size:    size,
		Mode:    mode,
		ModTime: modTime,
		IsDir:   false,
		Meta:    filesystem.MetaData{Name: PluginName, Type: fileType},
	}, nil
}

func (qfs *queueFS) Rename(oldPath, newPath string) error {
	return fmt.Errorf("cannot rename files in queuefs service")
}

func (qfs *queueFS) Chmod(path string, mode uint32) error {
	return fmt.Errorf("cannot change permissions in queuefs service")
}

func (qfs *queueFS) Open(path string) (io.ReadCloser, error) {
	data, err := qfs.Read(path, 0, -1)
	if err != nil {
		return nil, err
	}
	return io.NopCloser(bytes.NewReader(data)), nil
}

func (qfs *queueFS) OpenWrite(path string) (io.WriteCloser, error) {
	return &queueWriter{qfs: qfs, path: path, buf: &bytes.Buffer{}}, nil
}

type queueWriter struct {
	qfs  *queueFS
	path string
	buf  *bytes.Buffer
}

func (qw *queueWriter) Write(p []byte) (n int, err error) {
	return qw.buf.Write(p)
}

func (qw *queueWriter) Close() error {
	_, err := qw.qfs.Write(qw.path, qw.buf.Bytes(), -1, filesystem.WriteFlagAppend)
	return err
}

// Queue operations

func (qfs *queueFS) enqueue(queueName string, data []byte) ([]byte, error) {
	qfs.plugin.mu.Lock()
	defer qfs.plugin.mu.Unlock()

	now := time.Now()
	// Use UUIDv7 for globally unique and time-ordered message ID in distributed environments (e.g., TiDB backend)
	// UUIDv7 is time-sortable and ensures uniqueness across distributed systems
	msgUUID, err := uuid.NewV7()
	if err != nil {
		return nil, fmt.Errorf("failed to generate UUIDv7: %w", err)
	}
	msgID := msgUUID.String()
	msg := QueueMessage{
		ID:        msgID,
		Data:      string(data),
		Timestamp: now,
	}

	err = qfs.plugin.backend.Enqueue(queueName, msg)
	if err != nil {
		return nil, err
	}

	return []byte(msg.ID), nil
}

func (qfs *queueFS) dequeue(queueName string) ([]byte, error) {
	qfs.plugin.mu.Lock()
	defer qfs.plugin.mu.Unlock()

	msg, found, err := qfs.plugin.backend.Dequeue(queueName)
	if err != nil {
		return nil, err
	}

	if !found {
		// Return empty JSON object instead of error for empty queue
		return []byte("{}"), nil
	}

	return json.Marshal(msg)
}

func (qfs *queueFS) peek(queueName string) ([]byte, error) {
	qfs.plugin.mu.RLock()
	defer qfs.plugin.mu.RUnlock()

	msg, found, err := qfs.plugin.backend.Peek(queueName)
	if err != nil {
		return nil, err
	}

	if !found {
		// Return empty JSON object instead of error for empty queue
		return []byte("{}"), nil
	}

	return json.Marshal(msg)
}

func (qfs *queueFS) size(queueName string) ([]byte, error) {
	qfs.plugin.mu.RLock()
	defer qfs.plugin.mu.RUnlock()

	count, err := qfs.plugin.backend.Size(queueName)
	if err != nil {
		return nil, err
	}

	return []byte(strconv.Itoa(count)), nil
}

func (qfs *queueFS) clear(queueName string) error {
	qfs.plugin.mu.Lock()
	defer qfs.plugin.mu.Unlock()

	return qfs.plugin.backend.Clear(queueName)
}

// Ensure QueueFSPlugin implements ServicePlugin
var _ plugin.ServicePlugin = (*QueueFSPlugin)(nil)
var _ filesystem.FileSystem = (*queueFS)(nil)
var _ filesystem.HandleFS = (*queueFS)(nil)

// ============================================================================
// HandleFS Implementation for QueueFS
// ============================================================================

// queueFileHandle represents an open handle to a queue control file
type queueFileHandle struct {
	id        int64
	qfs       *queueFS
	path      string
	queueName string
	operation string // "enqueue", "dequeue", "peek", "size", "clear"
	flags     filesystem.OpenFlag

	// For dequeue/peek: cached message data (read once, return from cache)
	readBuffer []byte
	readDone   bool

	mu sync.Mutex
}

// handleManager manages open handles for queueFS
type handleManager struct {
	handles  map[int64]*queueFileHandle
	nextID   int64
	mu       sync.Mutex
}

// Global handle manager for queueFS (per plugin instance would be better, but keeping it simple)
var queueHandleManager = &handleManager{
	handles: make(map[int64]*queueFileHandle),
	nextID:  1,
}

// OpenHandle opens a file and returns a handle for stateful operations
func (qfs *queueFS) OpenHandle(path string, flags filesystem.OpenFlag, mode uint32) (filesystem.FileHandle, error) {
	queueName, operation, isDir, err := parseQueuePath(path)
	if err != nil {
		return nil, err
	}

	if isDir {
		return nil, fmt.Errorf("cannot open directory as file: %s", path)
	}

	if operation == "" {
		return nil, fmt.Errorf("cannot open queue directory: %s", path)
	}

	// Validate operation
	if !queueOperations[operation] {
		return nil, fmt.Errorf("unknown operation: %s", operation)
	}

	queueHandleManager.mu.Lock()
	defer queueHandleManager.mu.Unlock()

	id := queueHandleManager.nextID
	queueHandleManager.nextID++

	handle := &queueFileHandle{
		id:        id,
		qfs:       qfs,
		path:      path,
		queueName: queueName,
		operation: operation,
		flags:     flags,
	}

	queueHandleManager.handles[id] = handle
	return handle, nil
}

// GetHandle retrieves an existing handle by its ID
func (qfs *queueFS) GetHandle(id int64) (filesystem.FileHandle, error) {
	queueHandleManager.mu.Lock()
	defer queueHandleManager.mu.Unlock()

	handle, ok := queueHandleManager.handles[id]
	if !ok {
		return nil, filesystem.ErrNotFound
	}
	return handle, nil
}

// CloseHandle closes a handle by its ID
func (qfs *queueFS) CloseHandle(id int64) error {
	queueHandleManager.mu.Lock()
	defer queueHandleManager.mu.Unlock()

	handle, ok := queueHandleManager.handles[id]
	if !ok {
		return filesystem.ErrNotFound
	}

	delete(queueHandleManager.handles, id)
	_ = handle // Clear reference
	return nil
}

// ============================================================================
// FileHandle Implementation
// ============================================================================

func (h *queueFileHandle) ID() int64 {
	return h.id
}

func (h *queueFileHandle) Path() string {
	return h.path
}

func (h *queueFileHandle) Flags() filesystem.OpenFlag {
	return h.flags
}

func (h *queueFileHandle) Read(buf []byte) (int, error) {
	return h.ReadAt(buf, 0)
}

func (h *queueFileHandle) ReadAt(buf []byte, offset int64) (int, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	// For dequeue/peek: fetch data once and cache it
	if !h.readDone {
		var data []byte
		var err error

		switch h.operation {
		case "dequeue":
			data, err = h.qfs.dequeue(h.queueName)
		case "peek":
			data, err = h.qfs.peek(h.queueName)
		case "size":
			data, err = h.qfs.size(h.queueName)
		case "enqueue", "clear":
			// These are write-only operations
			return 0, io.EOF
		default:
			return 0, fmt.Errorf("unsupported read operation: %s", h.operation)
		}

		if err != nil {
			return 0, err
		}

		h.readBuffer = data
		h.readDone = true
	}

	// Return from cache
	if offset >= int64(len(h.readBuffer)) {
		return 0, io.EOF
	}

	n := copy(buf, h.readBuffer[offset:])
	return n, nil
}

func (h *queueFileHandle) Write(data []byte) (int, error) {
	return h.WriteAt(data, 0)
}

func (h *queueFileHandle) WriteAt(data []byte, offset int64) (int, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	switch h.operation {
	case "enqueue":
		_, err := h.qfs.enqueue(h.queueName, data)
		if err != nil {
			return 0, err
		}
		return len(data), nil
	case "clear":
		err := h.qfs.clear(h.queueName)
		if err != nil {
			return 0, err
		}
		return len(data), nil
	case "dequeue", "peek", "size":
		return 0, fmt.Errorf("cannot write to %s", h.operation)
	default:
		return 0, fmt.Errorf("unsupported write operation: %s", h.operation)
	}
}

func (h *queueFileHandle) Seek(offset int64, whence int) (int64, error) {
	// Queue files don't support seeking in the traditional sense
	// Just return 0 for compatibility
	return 0, nil
}

func (h *queueFileHandle) Sync() error {
	// Nothing to sync for queue operations
	return nil
}

func (h *queueFileHandle) Close() error {
	queueHandleManager.mu.Lock()
	defer queueHandleManager.mu.Unlock()

	delete(queueHandleManager.handles, h.id)
	return nil
}

func (h *queueFileHandle) Stat() (*filesystem.FileInfo, error) {
	return h.qfs.Stat(h.path)
}
