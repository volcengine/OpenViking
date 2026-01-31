package localfs

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/c4pt0r/agfs/agfs-server/pkg/filesystem"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugin"
	pluginConfig "github.com/c4pt0r/agfs/agfs-server/pkg/plugin/config"
	log "github.com/sirupsen/logrus"
)

const (
	PluginName = "localfs"
)

// LocalFS implements FileSystem interface using local file system as backend
type LocalFS struct {
	basePath   string // The local directory to mount
	mu         sync.RWMutex
	pluginName string
}

// NewLocalFS creates a new local file system
func NewLocalFS(basePath string) (*LocalFS, error) {
	// Resolve to absolute path
	absPath, err := filepath.Abs(basePath)
	if err != nil {
		return nil, fmt.Errorf("failed to resolve base path: %w", err)
	}

	// Check if base path exists
	info, err := os.Stat(absPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("base path does not exist: %s", absPath)
		}
		return nil, fmt.Errorf("failed to stat base path: %w", err)
	}

	if !info.IsDir() {
		return nil, fmt.Errorf("base path is not a directory: %s", absPath)
	}

	return &LocalFS{
		basePath:   absPath,
		pluginName: PluginName,
	}, nil
}

// resolvePath resolves a virtual path to the actual local path
func (fs *LocalFS) resolvePath(path string) string {
	// Remove leading slash to make it relative (VFS paths always start with /)
	relativePath := strings.TrimPrefix(path, "/")

	// Convert to OS-specific path separators
	// On Windows, this converts "/" to "\"
	relativePath = filepath.FromSlash(relativePath)

	// Clean the path
	relativePath = filepath.Clean(relativePath)

	if relativePath == "." {
		return fs.basePath
	}
	return filepath.Join(fs.basePath, relativePath)
}

func (fs *LocalFS) Create(path string) error {
	localPath := fs.resolvePath(path)

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Check if file already exists
	if _, err := os.Stat(localPath); err == nil {
		return fmt.Errorf("file already exists: %s", path)
	}

	// Check if parent directory exists
	parentDir := filepath.Dir(localPath)
	if _, err := os.Stat(parentDir); os.IsNotExist(err) {
		return fmt.Errorf("parent directory does not exist: %s", filepath.Dir(path))
	}

	// Create empty file
	f, err := os.Create(localPath)
	if err != nil {
		return fmt.Errorf("failed to create file: %w", err)
	}
	f.Close()

	return nil
}

func (fs *LocalFS) Mkdir(path string, perm uint32) error {
	localPath := fs.resolvePath(path)

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Check if directory already exists
	if _, err := os.Stat(localPath); err == nil {
		return fmt.Errorf("directory already exists: %s", path)
	}

	// Check if parent directory exists
	parentDir := filepath.Dir(localPath)
	if _, err := os.Stat(parentDir); os.IsNotExist(err) {
		return fmt.Errorf("parent directory does not exist: %s", filepath.Dir(path))
	}

	// Create directory
	err := os.Mkdir(localPath, os.FileMode(perm))
	if err != nil {
		return fmt.Errorf("failed to create directory: %w", err)
	}

	return nil
}

func (fs *LocalFS) Remove(path string) error {
	localPath := fs.resolvePath(path)

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Check if exists
	info, err := os.Stat(localPath)
	if err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("no such file or directory: %s", path)
		}
		return fmt.Errorf("failed to stat: %w", err)
	}

	// If directory, check if empty
	if info.IsDir() {
		entries, err := os.ReadDir(localPath)
		if err != nil {
			return fmt.Errorf("failed to read directory: %w", err)
		}
		if len(entries) > 0 {
			return fmt.Errorf("directory not empty: %s", path)
		}
	}

	// Remove file or empty directory
	err = os.Remove(localPath)
	if err != nil {
		return fmt.Errorf("failed to remove: %w", err)
	}

	return nil
}

func (fs *LocalFS) RemoveAll(path string) error {
	localPath := fs.resolvePath(path)

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Check if exists
	if _, err := os.Stat(localPath); os.IsNotExist(err) {
		return fmt.Errorf("no such file or directory: %s", path)
	}

	// Remove recursively
	err := os.RemoveAll(localPath)
	if err != nil {
		return fmt.Errorf("failed to remove: %w", err)
	}

	return nil
}

func (fs *LocalFS) Read(path string, offset int64, size int64) ([]byte, error) {
	localPath := fs.resolvePath(path)

	fs.mu.RLock()
	defer fs.mu.RUnlock()

	// Check if exists and is not a directory
	info, err := os.Stat(localPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("no such file: %s", path)
		}
		return nil, fmt.Errorf("failed to stat: %w", err)
	}

	if info.IsDir() {
		return nil, fmt.Errorf("is a directory: %s", path)
	}

	// Open file
	f, err := os.Open(localPath)
	if err != nil {
		return nil, fmt.Errorf("failed to open file: %w", err)
	}
	defer f.Close()

	// Get file size
	fileSize := info.Size()

	// Handle offset
	if offset < 0 {
		offset = 0
	}
	if offset >= fileSize {
		return []byte{}, io.EOF
	}

	// Seek to offset
	_, err = f.Seek(offset, 0)
	if err != nil {
		return nil, fmt.Errorf("failed to seek: %w", err)
	}

	// Determine read size
	readSize := size
	if size < 0 || offset+size > fileSize {
		readSize = fileSize - offset
	}

	// Read data
	data := make([]byte, readSize)
	n, err := io.ReadFull(f, data)
	if err != nil && err != io.EOF && err != io.ErrUnexpectedEOF {
		return nil, fmt.Errorf("failed to read: %w", err)
	}

	// Check if we reached end of file
	if offset+int64(n) >= fileSize {
		return data[:n], io.EOF
	}

	return data[:n], nil
}

func (fs *LocalFS) Write(path string, data []byte, offset int64, flags filesystem.WriteFlag) (int64, error) {
	localPath := fs.resolvePath(path)

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Check if it's a directory
	if info, err := os.Stat(localPath); err == nil && info.IsDir() {
		return 0, fmt.Errorf("is a directory: %s", path)
	}

	// Check if parent directory exists
	parentDir := filepath.Dir(localPath)
	if _, err := os.Stat(parentDir); os.IsNotExist(err) {
		return 0, fmt.Errorf("parent directory does not exist: %s", filepath.Dir(path))
	}

	// Build open flags
	openFlags := os.O_WRONLY
	if flags&filesystem.WriteFlagCreate != 0 {
		openFlags |= os.O_CREATE
	}
	if flags&filesystem.WriteFlagExclusive != 0 {
		openFlags |= os.O_EXCL
	}
	if flags&filesystem.WriteFlagTruncate != 0 {
		openFlags |= os.O_TRUNC
	}
	if flags&filesystem.WriteFlagAppend != 0 {
		openFlags |= os.O_APPEND
	}

	// Default behavior: create and truncate (like the old implementation)
	if flags == filesystem.WriteFlagNone && offset < 0 {
		openFlags |= os.O_CREATE | os.O_TRUNC
	}

	f, err := os.OpenFile(localPath, openFlags, 0644)
	if err != nil {
		return 0, fmt.Errorf("failed to open file: %w", err)
	}
	defer f.Close()

	var n int
	if offset >= 0 && flags&filesystem.WriteFlagAppend == 0 {
		// pwrite: write at specific offset
		n, err = f.WriteAt(data, offset)
	} else {
		// Normal write or append
		n, err = f.Write(data)
	}

	if err != nil {
		return 0, fmt.Errorf("failed to write: %w", err)
	}

	if flags&filesystem.WriteFlagSync != 0 {
		f.Sync()
	}

	return int64(n), nil
}

func (fs *LocalFS) ReadDir(path string) ([]filesystem.FileInfo, error) {
	localPath := fs.resolvePath(path)

	fs.mu.RLock()
	defer fs.mu.RUnlock()

	// Check if directory exists
	info, err := os.Stat(localPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("no such directory: %s", path)
		}
		return nil, fmt.Errorf("failed to stat: %w", err)
	}

	if !info.IsDir() {
		return nil, fmt.Errorf("not a directory: %s", path)
	}

	// Read directory
	entries, err := os.ReadDir(localPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read directory: %w", err)
	}

	var files []filesystem.FileInfo
	for _, entry := range entries {
		entryInfo, err := entry.Info()
		if err != nil {
			continue
		}

		files = append(files, filesystem.FileInfo{
			Name:    entry.Name(),
			Size:    entryInfo.Size(),
			Mode:    uint32(entryInfo.Mode()),
			ModTime: entryInfo.ModTime(),
			IsDir:   entry.IsDir(),
			Meta: filesystem.MetaData{
				Name: PluginName,
				Type: "local",
			},
		})
	}

	return files, nil
}

func (fs *LocalFS) Stat(path string) (*filesystem.FileInfo, error) {
	localPath := fs.resolvePath(path)

	fs.mu.RLock()
	defer fs.mu.RUnlock()

	// Get file info
	info, err := os.Stat(localPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("no such file or directory: %s", path)
		}
		return nil, fmt.Errorf("failed to stat: %w", err)
	}

	return &filesystem.FileInfo{
		Name:    info.Name(),
		Size:    info.Size(),
		Mode:    uint32(info.Mode()),
		ModTime: info.ModTime(),
		IsDir:   info.IsDir(),
		Meta: filesystem.MetaData{
			Name: PluginName,
			Type: "local",
			Content: map[string]string{
				"local_path": localPath,
			},
		},
	}, nil
}

func (fs *LocalFS) Rename(oldPath, newPath string) error {
	oldLocalPath := fs.resolvePath(oldPath)
	newLocalPath := fs.resolvePath(newPath)

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Check if old path exists
	if _, err := os.Stat(oldLocalPath); os.IsNotExist(err) {
		return fmt.Errorf("no such file or directory: %s", oldPath)
	}

	// Check if new path parent directory exists
	newParentDir := filepath.Dir(newLocalPath)
	if _, err := os.Stat(newParentDir); os.IsNotExist(err) {
		return fmt.Errorf("parent directory does not exist: %s", filepath.Dir(newPath))
	}

	// Rename/move
	err := os.Rename(oldLocalPath, newLocalPath)
	if err != nil {
		return fmt.Errorf("failed to rename: %w", err)
	}

	return nil
}

func (fs *LocalFS) Chmod(path string, mode uint32) error {
	localPath := fs.resolvePath(path)

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Check if exists
	if _, err := os.Stat(localPath); os.IsNotExist(err) {
		return fmt.Errorf("no such file or directory: %s", path)
	}

	// Change permissions
	err := os.Chmod(localPath, os.FileMode(mode))
	if err != nil {
		return fmt.Errorf("failed to chmod: %w", err)
	}

	return nil
}

func (fs *LocalFS) Open(path string) (io.ReadCloser, error) {
	localPath := fs.resolvePath(path)

	fs.mu.RLock()
	defer fs.mu.RUnlock()

	// Open file
	f, err := os.Open(localPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("no such file: %s", path)
		}
		return nil, fmt.Errorf("failed to open file: %w", err)
	}

	return f, nil
}

func (fs *LocalFS) OpenWrite(path string) (io.WriteCloser, error) {
	localPath := fs.resolvePath(path)

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Check if parent directory exists
	parentDir := filepath.Dir(localPath)
	if _, err := os.Stat(parentDir); os.IsNotExist(err) {
		return nil, fmt.Errorf("parent directory does not exist: %s", filepath.Dir(path))
	}

	// Open file for writing (create if not exists, truncate if exists)
	f, err := os.OpenFile(localPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0644)
	if err != nil {
		return nil, fmt.Errorf("failed to open file for writing: %w", err)
	}

	return f, nil
}

// localFSStreamReader implements filesystem.StreamReader for local files
type localFSStreamReader struct {
	file      *os.File
	chunkSize int64
	eof       bool
	mu        sync.Mutex
}

// ReadChunk reads the next chunk of data with a timeout
func (r *localFSStreamReader) ReadChunk(timeout time.Duration) ([]byte, bool, error) {
	r.mu.Lock()
	defer r.mu.Unlock()

	// If already reached EOF, return immediately
	if r.eof {
		return nil, true, io.EOF
	}

	// Create a channel for the read operation
	type readResult struct {
		data []byte
		n    int
		err  error
	}
	resultChan := make(chan readResult, 1)

	// Perform the read in a goroutine
	go func() {
		buf := make([]byte, r.chunkSize)
		n, err := r.file.Read(buf)
		resultChan <- readResult{data: buf, n: n, err: err}
	}()

	// Wait for either the read to complete or timeout
	select {
	case result := <-resultChan:
		if result.err != nil {
			if result.err == io.EOF {
				r.eof = true
				// If we read some data before EOF, return it
				if result.n > 0 {
					return result.data[:result.n], false, nil
				}
				return nil, true, io.EOF
			}
			return nil, false, result.err
		}

		// Check if this is the last chunk (partial read might indicate EOF)
		if result.n < int(r.chunkSize) {
			r.eof = true
		}

		return result.data[:result.n], r.eof, nil

	case <-time.After(timeout):
		// Note: the goroutine will continue reading and will be cleaned up when the file is closed
		return nil, false, fmt.Errorf("read timeout")
	}
}

// Close closes the file reader
func (r *localFSStreamReader) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()

	if r.file != nil {
		return r.file.Close()
	}
	return nil
}

// OpenStream implements the Streamer interface for streaming file reads
func (fs *LocalFS) OpenStream(path string) (filesystem.StreamReader, error) {
	localPath := fs.resolvePath(path)

	fs.mu.RLock()
	defer fs.mu.RUnlock()

	// Check if file exists and is not a directory
	info, err := os.Stat(localPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("no such file: %s", path)
		}
		return nil, fmt.Errorf("failed to stat: %w", err)
	}

	if info.IsDir() {
		return nil, fmt.Errorf("is a directory: %s", path)
	}

	// Open file for reading
	f, err := os.Open(localPath)
	if err != nil {
		return nil, fmt.Errorf("failed to open file: %w", err)
	}

	log.Infof("[localfs] Opened stream for file: %s (size: %d bytes)", path, info.Size())

	// Create and return stream reader with 64KB chunk size (matching streamfs default)
	return &localFSStreamReader{
		file:      f,
		chunkSize: 64 * 1024, // 64KB chunks
		eof:       false,
	}, nil
}

// LocalFSPlugin wraps LocalFS as a plugin
type LocalFSPlugin struct {
	fs       *LocalFS
	basePath string
}

// NewLocalFSPlugin creates a new LocalFS plugin
func NewLocalFSPlugin() *LocalFSPlugin {
	return &LocalFSPlugin{}
}

func (p *LocalFSPlugin) Name() string {
	return PluginName
}

func (p *LocalFSPlugin) Validate(cfg map[string]interface{}) error {
	// Check for unknown parameters
	allowedKeys := []string{"local_dir", "mount_path"}
	if err := pluginConfig.ValidateOnlyKnownKeys(cfg, allowedKeys); err != nil {
		return err
	}

	// Validate local_dir parameter
	basePath, ok := cfg["local_dir"].(string)
	if !ok || basePath == "" {
		return fmt.Errorf("local_dir is required in configuration")
	}

	// Resolve to absolute path
	absPath, err := filepath.Abs(basePath)
	if err != nil {
		return fmt.Errorf("failed to resolve base path: %w", err)
	}

	// Check if path exists
	info, err := os.Stat(absPath)
	if err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("base path does not exist: %s", absPath)
		}
		return fmt.Errorf("failed to stat base path: %w", err)
	}

	// Verify it's a directory
	if !info.IsDir() {
		return fmt.Errorf("base path is not a directory: %s", absPath)
	}

	return nil
}

func (p *LocalFSPlugin) Initialize(config map[string]interface{}) error {
	// Parse configuration (validation already done in Validate)
	basePath := config["local_dir"].(string)
	p.basePath = basePath

	// Create LocalFS instance
	fs, err := NewLocalFS(basePath)
	if err != nil {
		return fmt.Errorf("failed to initialize localfs: %w", err)
	}
	p.fs = fs

	log.Infof("[localfs] Initialized with base path: %s", basePath)
	return nil
}

func (p *LocalFSPlugin) GetFileSystem() filesystem.FileSystem {
	return p.fs
}

func (p *LocalFSPlugin) GetReadme() string {
	readmeContent := fmt.Sprintf(`LocalFS Plugin - Local File System Mount

This plugin mounts a local directory into the AGFS virtual file system.

FEATURES:
  - Mount any local directory into AGFS
  - Full POSIX file system operations
  - Direct access to local files and directories
  - Preserves file permissions and timestamps
  - Efficient file operations (no copying)

CONFIGURATION:

  Basic configuration:
  [plugins.localfs]
  enabled = true
  path = "/local"

    [plugins.localfs.config]
    local_dir = "/path/to/local/directory"

  Multiple local mounts:
  [plugins.localfs_home]
  enabled = true
  path = "/home"

    [plugins.localfs_home.config]
    local_dir = "/Users/username"

  [plugins.localfs_data]
  enabled = true
  path = "/data"

    [plugins.localfs_data.config]
    local_dir = "/var/data"

CURRENT MOUNT:
  Base Path: %s

USAGE:

  List directory:
    agfs ls /local

  Read a file:
    agfs cat /local/file.txt

  Write to a file:
    agfs write /local/file.txt "Hello, World!"

  Create a directory:
    agfs mkdir /local/newdir

  Remove a file:
    agfs rm /local/file.txt

  Remove directory recursively:
    agfs rm -r /local/olddir

  Move/rename:
    agfs mv /local/old.txt /local/new.txt

  Change permissions:
    agfs chmod 755 /local/script.sh

EXAMPLES:

  # Basic file operations
  agfs:/> ls /local
  file1.txt  dir1/  dir2/

  agfs:/> cat /local/file1.txt
  Hello from local filesystem!

  agfs:/> echo "new content" > /local/file2.txt
  Written 12 bytes to /local/file2.txt

  # Directory operations
  agfs:/> mkdir /local/newdir
  agfs:/> ls /local
  file1.txt  file2.txt  dir1/  dir2/  newdir/

NOTES:
  - Changes are directly applied to the local file system
  - File permissions are preserved and can be modified
  - Symlinks are followed by default
  - Be careful with rm -r as it permanently deletes files

USE CASES:
  - Access local configuration files
  - Process local data files
  - Integrate with existing file-based workflows
  - Development and testing with local data
  - Backup and sync operations

ADVANTAGES:
  - No data copying overhead
  - Direct access to local files
  - Preserves all file system metadata
  - Supports all standard file operations
  - Efficient for large files

VERSION: 1.0.0
AUTHOR: AGFS Server
`, p.basePath)

	return readmeContent
}

func (p *LocalFSPlugin) GetConfigParams() []plugin.ConfigParameter {
	return []plugin.ConfigParameter{
		{
			Name:        "local_dir",
			Type:        "string",
			Required:    true,
			Default:     "",
			Description: "Local directory path to expose (must exist)",
		},
	}
}

func (p *LocalFSPlugin) Shutdown() error {
	log.Infof("[localfs] Shutting down")
	return nil
}

// Ensure LocalFSPlugin implements ServicePlugin
var _ plugin.ServicePlugin = (*LocalFSPlugin)(nil)
var _ filesystem.FileSystem = (*LocalFS)(nil)
