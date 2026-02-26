package memfs

import (
	"bytes"
	"fmt"
	"io"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/c4pt0r/agfs/agfs-server/pkg/filesystem"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugin"
)

// Meta values for MemFS plugin
const (
	MetaValueDir  = "dir"
	MetaValueFile = "file"
)

// Node represents a file or directory in memory
type Node struct {
	Name     string
	IsDir    bool
	Data     []byte
	Mode     uint32
	ModTime  time.Time
	Children map[string]*Node
}

// MemoryFS implements FileSystem and HandleFS interfaces with in-memory storage
type MemoryFS struct {
	root       *Node
	mu         sync.RWMutex
	pluginName string

	// Handle management
	handles      map[int64]*MemoryFileHandle
	handlesMu    sync.RWMutex
	nextHandleID int64
}

// NewMemoryFS creates a new in-memory file system
func NewMemoryFS() *MemoryFS {
	return NewMemoryFSWithPlugin("")
}

// NewMemoryFSWithPlugin creates a new in-memory file system with a plugin name
func NewMemoryFSWithPlugin(pluginName string) *MemoryFS {
	return &MemoryFS{
		root: &Node{
			Name:     "/",
			IsDir:    true,
			Mode:     0755,
			ModTime:  time.Now(),
			Children: make(map[string]*Node),
		},
		pluginName:   pluginName,
		handles:      make(map[int64]*MemoryFileHandle),
		nextHandleID: 1,
	}
}

// getNode retrieves a node from the tree
func (mfs *MemoryFS) getNode(path string) (*Node, error) {
	path = filesystem.NormalizePath(path)

	if path == "/" {
		return mfs.root, nil
	}

	parts := strings.Split(strings.Trim(path, "/"), "/")
	current := mfs.root

	for _, part := range parts {
		if !current.IsDir {
			return nil, fmt.Errorf("not a directory: %s", path)
		}
		next, exists := current.Children[part]
		if !exists {
			return nil, filesystem.NewNotFoundError("getNode", path)
		}
		current = next
	}

	return current, nil
}

// getParentNode retrieves the parent node and the basename
func (mfs *MemoryFS) getParentNode(path string) (*Node, string, error) {
	path = filesystem.NormalizePath(path)

	if path == "/" {
		return nil, "", fmt.Errorf("cannot get parent of root")
	}

	dir := filepath.Dir(path)
	base := filepath.Base(path)

	parent, err := mfs.getNode(dir)
	if err != nil {
		return nil, "", err
	}

	if !parent.IsDir {
		return nil, "", fmt.Errorf("parent is not a directory")
	}

	return parent, base, nil
}

// Create creates a new file
func (mfs *MemoryFS) Create(path string) error {
	mfs.mu.Lock()
	defer mfs.mu.Unlock()

	parent, name, err := mfs.getParentNode(path)
	if err != nil {
		return err
	}

	if _, exists := parent.Children[name]; exists {
		return fmt.Errorf("file already exists: %s", path)
	}

	parent.Children[name] = &Node{
		Name:     name,
		IsDir:    false,
		Data:     []byte{},
		Mode:     0644,
		ModTime:  time.Now(),
		Children: nil,
	}

	return nil
}

// Mkdir creates a new directory
func (mfs *MemoryFS) Mkdir(path string, perm uint32) error {
	mfs.mu.Lock()
	defer mfs.mu.Unlock()

	parent, name, err := mfs.getParentNode(path)
	if err != nil {
		return err
	}

	if _, exists := parent.Children[name]; exists {
		return fmt.Errorf("directory already exists: %s", path)
	}

	parent.Children[name] = &Node{
		Name:     name,
		IsDir:    true,
		Mode:     perm,
		ModTime:  time.Now(),
		Children: make(map[string]*Node),
	}

	return nil
}

// Remove removes a file or empty directory
func (mfs *MemoryFS) Remove(path string) error {
	mfs.mu.Lock()
	defer mfs.mu.Unlock()

	if filesystem.NormalizePath(path) == "/" {
		return fmt.Errorf("cannot remove root directory")
	}

	parent, name, err := mfs.getParentNode(path)
	if err != nil {
		return err
	}

	node, exists := parent.Children[name]
	if !exists {
		return filesystem.NewNotFoundError("remove", path)
	}

	if node.IsDir && len(node.Children) > 0 {
		return fmt.Errorf("directory not empty: %s", path)
	}

	delete(parent.Children, name)
	return nil
}

// RemoveAll removes a path and any children it contains
func (mfs *MemoryFS) RemoveAll(path string) error {
	mfs.mu.Lock()
	defer mfs.mu.Unlock()

	// If path is root, remove all children but not the root itself
	if filesystem.NormalizePath(path) == "/" {
		mfs.root.Children = make(map[string]*Node)
		return nil
	}

	parent, name, err := mfs.getParentNode(path)
	if err != nil {
		return err
	}

	if _, exists := parent.Children[name]; !exists {
		return filesystem.NewNotFoundError("remove", path)
	}

	delete(parent.Children, name)
	return nil
}

// Read reads file content with optional offset and size
func (mfs *MemoryFS) Read(path string, offset int64, size int64) ([]byte, error) {
	mfs.mu.RLock()
	defer mfs.mu.RUnlock()

	node, err := mfs.getNode(path)
	if err != nil {
		return nil, err
	}

	if node.IsDir {
		return nil, fmt.Errorf("is a directory: %s", path)
	}

	return plugin.ApplyRangeRead(node.Data, offset, size)
}

// Write writes data to a file with optional offset and flags
func (mfs *MemoryFS) Write(path string, data []byte, offset int64, flags filesystem.WriteFlag) (int64, error) {
	mfs.mu.Lock()
	defer mfs.mu.Unlock()

	parent, name, err := mfs.getParentNode(path)
	if err != nil {
		if flags&filesystem.WriteFlagCreate == 0 {
			return 0, err
		}
		// Try to get parent again - maybe it doesn't exist
		return 0, err
	}

	node, exists := parent.Children[name]

	// Handle exclusive flag
	if exists && flags&filesystem.WriteFlagExclusive != 0 {
		return 0, fmt.Errorf("file already exists: %s", path)
	}

	if !exists {
		if flags&filesystem.WriteFlagCreate == 0 {
			return 0, fmt.Errorf("file not found: %s", path)
		}
		// Create the file
		node = &Node{
			Name:     name,
			IsDir:    false,
			Data:     []byte{},
			Mode:     0644,
			ModTime:  time.Now(),
			Children: nil,
		}
		parent.Children[name] = node
	}

	if node.IsDir {
		return 0, fmt.Errorf("is a directory: %s", path)
	}

	// Handle truncate flag
	if flags&filesystem.WriteFlagTruncate != 0 {
		node.Data = []byte{}
	}

	// Handle append flag
	if flags&filesystem.WriteFlagAppend != 0 {
		offset = int64(len(node.Data))
	}

	// Handle offset write
	if offset < 0 {
		// Overwrite mode (default): replace entire content
		node.Data = data
	} else {
		// Offset write mode
		newSize := offset + int64(len(data))
		if newSize > int64(len(node.Data)) {
			newData := make([]byte, newSize)
			copy(newData, node.Data)
			node.Data = newData
		}
		copy(node.Data[offset:], data)
	}

	node.ModTime = time.Now()

	return int64(len(data)), nil
}

// ReadDir lists the contents of a directory
func (mfs *MemoryFS) ReadDir(path string) ([]filesystem.FileInfo, error) {
	mfs.mu.RLock()
	defer mfs.mu.RUnlock()

	node, err := mfs.getNode(path)
	if err != nil {
		return nil, err
	}

	if !node.IsDir {
		return nil, fmt.Errorf("not a directory: %s", path)
	}

	var infos []filesystem.FileInfo
	for _, child := range node.Children {
		metaType := MetaValueFile
		if child.IsDir {
			metaType = MetaValueDir
		}

		infos = append(infos, filesystem.FileInfo{
			Name:    child.Name,
			Size:    int64(len(child.Data)),
			Mode:    child.Mode,
			ModTime: child.ModTime,
			IsDir:   child.IsDir,
			Meta: filesystem.MetaData{
				Name: mfs.pluginName,
				Type: metaType,
			},
		})
	}

	return infos, nil
}

// Stat returns file information
func (mfs *MemoryFS) Stat(path string) (*filesystem.FileInfo, error) {
	mfs.mu.RLock()
	defer mfs.mu.RUnlock()

	node, err := mfs.getNode(path)
	if err != nil {
		return nil, err
	}

	metaType := MetaValueFile
	if node.IsDir {
		metaType = MetaValueDir
	}

	return &filesystem.FileInfo{
		Name:    node.Name,
		Size:    int64(len(node.Data)),
		Mode:    node.Mode,
		ModTime: node.ModTime,
		IsDir:   node.IsDir,
		Meta: filesystem.MetaData{
			Name: mfs.pluginName,
			Type: metaType,
		},
	}, nil
}

// Rename renames/moves a file or directory
func (mfs *MemoryFS) Rename(oldPath, newPath string) error {
	mfs.mu.Lock()
	defer mfs.mu.Unlock()

	oldParent, oldName, err := mfs.getParentNode(oldPath)
	if err != nil {
		return err
	}

	node, exists := oldParent.Children[oldName]
	if !exists {
		return filesystem.NewNotFoundError("rename", oldPath)
	}

	newParent, newName, err := mfs.getParentNode(newPath)
	if err != nil {
		return err
	}

	if _, exists := newParent.Children[newName]; exists {
		return fmt.Errorf("file already exists: %s", newPath)
	}

	// Move the node
	delete(oldParent.Children, oldName)
	node.Name = newName
	newParent.Children[newName] = node

	return nil
}

// Chmod changes file permissions
func (mfs *MemoryFS) Chmod(path string, mode uint32) error {
	mfs.mu.Lock()
	defer mfs.mu.Unlock()

	node, err := mfs.getNode(path)
	if err != nil {
		return err
	}

	node.Mode = mode
	return nil
}

// memoryReadCloser wraps a bytes.Reader to implement io.ReadCloser
type memoryReadCloser struct {
	*bytes.Reader
}

func (m *memoryReadCloser) Close() error {
	return nil
}

// Open opens a file for reading
func (mfs *MemoryFS) Open(path string) (io.ReadCloser, error) {
	data, err := mfs.Read(path, 0, -1)
	if err != nil {
		return nil, err
	}
	return &memoryReadCloser{bytes.NewReader(data)}, nil
}

// memoryWriteCloser implements io.WriteCloser for in-memory files
type memoryWriteCloser struct {
	buffer *bytes.Buffer
	mfs    *MemoryFS
	path   string
}

func (m *memoryWriteCloser) Write(p []byte) (n int, err error) {
	return m.buffer.Write(p)
}

func (m *memoryWriteCloser) Close() error {
	_, err := m.mfs.Write(m.path, m.buffer.Bytes(), -1, filesystem.WriteFlagCreate|filesystem.WriteFlagTruncate)
	return err
}

// OpenWrite opens a file for writing
func (mfs *MemoryFS) OpenWrite(path string) (io.WriteCloser, error) {
	return &memoryWriteCloser{
		buffer: &bytes.Buffer{},
		mfs:    mfs,
		path:   path,
	}, nil
}

// ============================================================================
// HandleFS Implementation
// ============================================================================

// MemoryFileHandle implements FileHandle for in-memory files
type MemoryFileHandle struct {
	id     int64
	path   string
	flags  filesystem.OpenFlag
	mfs    *MemoryFS
	pos    int64
	closed bool
	mu     sync.Mutex
}

// ID returns the unique identifier of this handle
func (h *MemoryFileHandle) ID() int64 {
	return h.id
}

// Path returns the file path this handle is associated with
func (h *MemoryFileHandle) Path() string {
	return h.path
}

// Flags returns the open flags used when opening this handle
func (h *MemoryFileHandle) Flags() filesystem.OpenFlag {
	return h.flags
}

// Read reads up to len(buf) bytes from the current position
func (h *MemoryFileHandle) Read(buf []byte) (int, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if h.closed {
		return 0, fmt.Errorf("handle closed")
	}

	// Check read permission
	accessMode := h.flags & 0x3
	if accessMode != filesystem.O_RDONLY && accessMode != filesystem.O_RDWR {
		return 0, fmt.Errorf("handle not opened for reading")
	}

	h.mfs.mu.RLock()
	defer h.mfs.mu.RUnlock()

	node, err := h.mfs.getNode(h.path)
	if err != nil {
		return 0, err
	}

	if h.pos >= int64(len(node.Data)) {
		return 0, io.EOF
	}

	n := copy(buf, node.Data[h.pos:])
	h.pos += int64(n)
	return n, nil
}

// ReadAt reads len(buf) bytes from the specified offset (pread)
func (h *MemoryFileHandle) ReadAt(buf []byte, offset int64) (int, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if h.closed {
		return 0, fmt.Errorf("handle closed")
	}

	// Check read permission
	accessMode := h.flags & 0x3
	if accessMode != filesystem.O_RDONLY && accessMode != filesystem.O_RDWR {
		return 0, fmt.Errorf("handle not opened for reading")
	}

	h.mfs.mu.RLock()
	defer h.mfs.mu.RUnlock()

	node, err := h.mfs.getNode(h.path)
	if err != nil {
		return 0, err
	}

	if offset >= int64(len(node.Data)) {
		return 0, io.EOF
	}

	n := copy(buf, node.Data[offset:])
	return n, nil
}

// Write writes data at the current position
func (h *MemoryFileHandle) Write(data []byte) (int, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if h.closed {
		return 0, fmt.Errorf("handle closed")
	}

	// Check write permission
	accessMode := h.flags & 0x3
	if accessMode != filesystem.O_WRONLY && accessMode != filesystem.O_RDWR {
		return 0, fmt.Errorf("handle not opened for writing")
	}

	h.mfs.mu.Lock()
	defer h.mfs.mu.Unlock()

	node, err := h.mfs.getNode(h.path)
	if err != nil {
		return 0, err
	}

	// Handle append mode
	writePos := h.pos
	if h.flags&filesystem.O_APPEND != 0 {
		writePos = int64(len(node.Data))
	}

	// Extend data if necessary
	newSize := writePos + int64(len(data))
	if newSize > int64(len(node.Data)) {
		newData := make([]byte, newSize)
		copy(newData, node.Data)
		node.Data = newData
	}

	copy(node.Data[writePos:], data)
	h.pos = writePos + int64(len(data))
	node.ModTime = time.Now()

	return len(data), nil
}

// WriteAt writes data at the specified offset (pwrite)
func (h *MemoryFileHandle) WriteAt(data []byte, offset int64) (int, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if h.closed {
		return 0, fmt.Errorf("handle closed")
	}

	// Check write permission
	accessMode := h.flags & 0x3
	if accessMode != filesystem.O_WRONLY && accessMode != filesystem.O_RDWR {
		return 0, fmt.Errorf("handle not opened for writing")
	}

	h.mfs.mu.Lock()
	defer h.mfs.mu.Unlock()

	node, err := h.mfs.getNode(h.path)
	if err != nil {
		return 0, err
	}

	// Extend data if necessary
	newSize := offset + int64(len(data))
	if newSize > int64(len(node.Data)) {
		newData := make([]byte, newSize)
		copy(newData, node.Data)
		node.Data = newData
	}

	copy(node.Data[offset:], data)
	node.ModTime = time.Now()

	return len(data), nil
}

// Seek moves the read/write position
func (h *MemoryFileHandle) Seek(offset int64, whence int) (int64, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if h.closed {
		return 0, fmt.Errorf("handle closed")
	}

	h.mfs.mu.RLock()
	node, err := h.mfs.getNode(h.path)
	h.mfs.mu.RUnlock()
	if err != nil {
		return 0, err
	}

	var newPos int64
	switch whence {
	case io.SeekStart:
		newPos = offset
	case io.SeekCurrent:
		newPos = h.pos + offset
	case io.SeekEnd:
		newPos = int64(len(node.Data)) + offset
	default:
		return 0, fmt.Errorf("invalid whence: %d", whence)
	}

	if newPos < 0 {
		return 0, fmt.Errorf("negative position")
	}

	h.pos = newPos
	return h.pos, nil
}

// Sync synchronizes the file data to storage (no-op for in-memory)
func (h *MemoryFileHandle) Sync() error {
	h.mu.Lock()
	defer h.mu.Unlock()

	if h.closed {
		return fmt.Errorf("handle closed")
	}
	// No-op for in-memory storage
	return nil
}

// Close closes the handle and releases resources
func (h *MemoryFileHandle) Close() error {
	h.mu.Lock()
	defer h.mu.Unlock()

	if h.closed {
		return nil
	}

	h.closed = true

	// Remove from MemoryFS handles map
	h.mfs.handlesMu.Lock()
	delete(h.mfs.handles, h.id)
	h.mfs.handlesMu.Unlock()

	return nil
}

// Stat returns file information
func (h *MemoryFileHandle) Stat() (*filesystem.FileInfo, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if h.closed {
		return nil, fmt.Errorf("handle closed")
	}

	return h.mfs.Stat(h.path)
}

// OpenHandle opens a file and returns a handle for stateful operations
func (mfs *MemoryFS) OpenHandle(path string, flags filesystem.OpenFlag, mode uint32) (filesystem.FileHandle, error) {
	mfs.mu.Lock()
	defer mfs.mu.Unlock()

	path = filesystem.NormalizePath(path)

	// Check if file exists
	node, err := mfs.getNode(path)
	fileExists := err == nil && node != nil

	// Handle O_EXCL: fail if file exists
	if flags&filesystem.O_EXCL != 0 && fileExists {
		return nil, fmt.Errorf("file already exists: %s", path)
	}

	// Handle O_CREATE: create file if it doesn't exist
	if flags&filesystem.O_CREATE != 0 && !fileExists {
		parent, name, err := mfs.getParentNode(path)
		if err != nil {
			return nil, fmt.Errorf("parent directory not found: %s", path)
		}
		node = &Node{
			Name:     name,
			IsDir:    false,
			Data:     []byte{},
			Mode:     mode,
			ModTime:  time.Now(),
			Children: nil,
		}
		parent.Children[name] = node
	} else if !fileExists {
		return nil, fmt.Errorf("file not found: %s", path)
	}

	if node.IsDir {
		return nil, fmt.Errorf("is a directory: %s", path)
	}

	// Handle O_TRUNC: truncate file
	if flags&filesystem.O_TRUNC != 0 {
		node.Data = []byte{}
		node.ModTime = time.Now()
	}

	// Create handle with auto-incremented ID
	mfs.handlesMu.Lock()
	handleID := mfs.nextHandleID
	mfs.nextHandleID++
	handle := &MemoryFileHandle{
		id:    handleID,
		path:  path,
		flags: flags,
		mfs:   mfs,
		pos:   0,
	}
	mfs.handles[handleID] = handle
	mfs.handlesMu.Unlock()

	return handle, nil
}

// GetHandle retrieves an existing handle by its ID
func (mfs *MemoryFS) GetHandle(id int64) (filesystem.FileHandle, error) {
	mfs.handlesMu.RLock()
	defer mfs.handlesMu.RUnlock()

	handle, exists := mfs.handles[id]
	if !exists {
		return nil, filesystem.ErrNotFound
	}

	return handle, nil
}

// CloseHandle closes a handle by its ID
func (mfs *MemoryFS) CloseHandle(id int64) error {
	mfs.handlesMu.RLock()
	handle, exists := mfs.handles[id]
	mfs.handlesMu.RUnlock()

	if !exists {
		return filesystem.ErrNotFound
	}

	return handle.Close()
}

// Ensure MemoryFS implements HandleFS interface
var _ filesystem.HandleFS = (*MemoryFS)(nil)

