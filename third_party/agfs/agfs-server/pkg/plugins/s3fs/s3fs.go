package s3fs

import (
	"context"
	"fmt"
	"io"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/c4pt0r/agfs/agfs-server/pkg/filesystem"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugin"
	"github.com/c4pt0r/agfs/agfs-server/pkg/plugin/config"
	log "github.com/sirupsen/logrus"
)

const (
	PluginName = "s3fs"
)

// S3FS implements FileSystem interface using AWS S3 as backend
type S3FS struct {
	client     *S3Client
	mu         sync.RWMutex
	pluginName string

	// Caches for performance optimization
	dirCache  *ListDirCache
	statCache *StatCache
}

// CacheConfig holds cache configuration
type CacheConfig struct {
	Enabled      bool
	DirCacheTTL  time.Duration
	StatCacheTTL time.Duration
	MaxSize      int
}

// DefaultCacheConfig returns default cache configuration
func DefaultCacheConfig() CacheConfig {
	return CacheConfig{
		Enabled:      true,
		DirCacheTTL:  30 * time.Second,
		StatCacheTTL: 60 * time.Second,
		MaxSize:      1000,
	}
}

// NewS3FS creates a new S3-backed file system
func NewS3FS(cfg S3Config) (*S3FS, error) {
	return NewS3FSWithCache(cfg, DefaultCacheConfig())
}

// NewS3FSWithCache creates a new S3-backed file system with cache configuration
func NewS3FSWithCache(cfg S3Config, cacheCfg CacheConfig) (*S3FS, error) {
	client, err := NewS3Client(cfg)
	if err != nil {
		return nil, fmt.Errorf("failed to create S3 client: %w", err)
	}

	return &S3FS{
		client:     client,
		pluginName: PluginName,
		dirCache:   NewListDirCache(cacheCfg.MaxSize, cacheCfg.DirCacheTTL, cacheCfg.Enabled),
		statCache:  NewStatCache(cacheCfg.MaxSize*5, cacheCfg.StatCacheTTL, cacheCfg.Enabled),
	}, nil
}

func (fs *S3FS) Create(path string) error {
	path = filesystem.NormalizeS3Key(path)
	ctx := context.Background()

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Check if file already exists
	exists, err := fs.client.ObjectExists(ctx, path)
	if err != nil {
		return fmt.Errorf("failed to check if file exists: %w", err)
	}
	if exists {
		return fmt.Errorf("file already exists: %s", path)
	}

	// Check if parent directory exists
	parent := getParentPath(path)
	if parent != "" {
		dirExists, err := fs.client.DirectoryExists(ctx, parent)
		if err != nil {
			return fmt.Errorf("failed to check parent directory: %w", err)
		}
		if !dirExists {
			return fmt.Errorf("parent directory does not exist: %s", parent)
		}
	}

	// Create empty file
	err = fs.client.PutObject(ctx, path, []byte{})
	if err == nil {
		// Invalidate caches
		fs.dirCache.Invalidate(parent)
		fs.statCache.Invalidate(path)
	}
	return err
}

func (fs *S3FS) Mkdir(path string, perm uint32) error {
	path = filesystem.NormalizeS3Key(path)
	ctx := context.Background()

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Check if directory already exists
	exists, err := fs.client.DirectoryExists(ctx, path)
	if err != nil {
		return fmt.Errorf("failed to check if directory exists: %w", err)
	}
	if exists {
		return fmt.Errorf("directory already exists: %s", path)
	}

	// Check if parent directory exists
	parent := getParentPath(path)
	if parent != "" {
		dirExists, err := fs.client.DirectoryExists(ctx, parent)
		if err != nil {
			return fmt.Errorf("failed to check parent directory: %w", err)
		}
		if !dirExists {
			return fmt.Errorf("parent directory does not exist: %s", parent)
		}
	}

	// Create directory marker
	err = fs.client.CreateDirectory(ctx, path)
	if err == nil {
		// Invalidate caches
		fs.dirCache.Invalidate(parent)
		fs.statCache.Invalidate(path)
	}
	return err
}

func (fs *S3FS) Remove(path string) error {
	path = filesystem.NormalizeS3Key(path)
	ctx := context.Background()

	fs.mu.Lock()
	defer fs.mu.Unlock()

	parent := getParentPath(path)

	// Check if it's a file
	exists, err := fs.client.ObjectExists(ctx, path)
	if err != nil {
		return fmt.Errorf("failed to check if file exists: %w", err)
	}

	if exists {
		// It's a file, delete it
		err = fs.client.DeleteObject(ctx, path)
		if err == nil {
			fs.dirCache.Invalidate(parent)
			fs.statCache.Invalidate(path)
		}
		return err
	}

	// Check if it's a directory
	dirExists, err := fs.client.DirectoryExists(ctx, path)
	if err != nil {
		return fmt.Errorf("failed to check if directory exists: %w", err)
	}

	if !dirExists {
		return filesystem.ErrNotFound
	}

	// Check if directory is empty
	objects, err := fs.client.ListObjects(ctx, path)
	if err != nil {
		return fmt.Errorf("failed to list directory: %w", err)
	}

	if len(objects) > 0 {
		return fmt.Errorf("directory not empty: %s", path)
	}

	// Delete directory marker
	err = fs.client.DeleteObject(ctx, path+"/")
	if err == nil {
		fs.dirCache.Invalidate(parent)
		fs.dirCache.Invalidate(path)
		fs.statCache.Invalidate(path)
	}
	return err
}

func (fs *S3FS) RemoveAll(path string) error {
	path = filesystem.NormalizeS3Key(path)
	ctx := context.Background()

	fs.mu.Lock()
	defer fs.mu.Unlock()

	err := fs.client.DeleteDirectory(ctx, path)
	if err == nil {
		parent := getParentPath(path)
		fs.dirCache.Invalidate(parent)
		fs.dirCache.InvalidatePrefix(path)
		fs.statCache.InvalidatePrefix(path)
	}
	return err
}

func (fs *S3FS) Read(path string, offset int64, size int64) ([]byte, error) {
	path = filesystem.NormalizeS3Key(path)
	ctx := context.Background()

	fs.mu.RLock()
	defer fs.mu.RUnlock()

	// Use S3 Range request for efficient partial reads
	if offset > 0 || size > 0 {
		data, err := fs.client.GetObjectRange(ctx, path, offset, size)
		if err != nil {
			if strings.Contains(err.Error(), "NoSuchKey") || strings.Contains(err.Error(), "NotFound") {
				return nil, filesystem.ErrNotFound
			}
			return nil, err
		}
		return data, nil
	}

	// Full file read
	data, err := fs.client.GetObject(ctx, path)
	if err != nil {
		if strings.Contains(err.Error(), "NoSuchKey") || strings.Contains(err.Error(), "NotFound") {
			return nil, filesystem.ErrNotFound
		}
		return nil, err
	}

	return data, nil
}

func (fs *S3FS) Write(path string, data []byte, offset int64, flags filesystem.WriteFlag) (int64, error) {
	path = filesystem.NormalizeS3Key(path)
	ctx := context.Background()

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// S3 is an object store - it doesn't support offset writes
	// Only full object replacement is supported
	if offset >= 0 && offset != 0 {
		return 0, fmt.Errorf("S3 does not support offset writes")
	}

	// Skip directory checks for performance - S3 PutObject will overwrite anyway
	// The path ending with "/" check is sufficient for directory detection
	if strings.HasSuffix(path, "/") {
		return 0, fmt.Errorf("is a directory: %s", path)
	}

	// Write to S3 directly - S3 will create parent "directories" implicitly
	err := fs.client.PutObject(ctx, path, data)
	if err != nil {
		return 0, err
	}

	// Invalidate caches
	parent := getParentPath(path)
	fs.dirCache.Invalidate(parent)
	fs.statCache.Invalidate(path)

	return int64(len(data)), nil
}

func (fs *S3FS) ReadDir(path string) ([]filesystem.FileInfo, error) {
	path = filesystem.NormalizeS3Key(path)
	ctx := context.Background()

	fs.mu.RLock()
	defer fs.mu.RUnlock()

	// Check cache first
	if cached, ok := fs.dirCache.Get(path); ok {
		return cached, nil
	}

	// Check if directory exists
	if path != "" {
		exists, err := fs.client.DirectoryExists(ctx, path)
		if err != nil {
			return nil, fmt.Errorf("failed to check directory: %w", err)
		}
		if !exists {
			return nil, filesystem.ErrNotFound
		}
	}

	// List objects
	objects, err := fs.client.ListObjects(ctx, path)
	if err != nil {
		return nil, err
	}

	var files []filesystem.FileInfo
	for _, obj := range objects {
		mode := uint32(0644)
		if obj.IsDir {
			mode = 0755
		}
		files = append(files, filesystem.FileInfo{
			Name:    obj.Key,
			Size:    obj.Size,
			Mode:    mode,
			ModTime: obj.LastModified,
			IsDir:   obj.IsDir,
			Meta: filesystem.MetaData{
				Name: PluginName,
				Type: "s3",
			},
		})
	}

	// Cache the result
	fs.dirCache.Put(path, files)

	return files, nil
}

func (fs *S3FS) Stat(path string) (*filesystem.FileInfo, error) {
	path = filesystem.NormalizeS3Key(path)
	ctx := context.Background()

	fs.mu.RLock()
	defer fs.mu.RUnlock()

	// Special case for root
	if path == "" {
		return &filesystem.FileInfo{
			Name:    "/",
			Size:    0,
			Mode:    0755,
			ModTime: time.Now(),
			IsDir:   true,
			Meta: filesystem.MetaData{
				Name: PluginName,
				Type: "s3",
				Content: map[string]string{
					"region": fs.client.region,
					"bucket": fs.client.bucket,
					"prefix": fs.client.prefix,
				},
			},
		}, nil
	}

	// Check cache first
	if cached, ok := fs.statCache.Get(path); ok {
		return cached, nil
	}

	// Try as file first
	head, err := fs.client.HeadObject(ctx, path)
	if err == nil {
		info := &filesystem.FileInfo{
			Name:    filepath.Base(path),
			Size:    aws.ToInt64(head.ContentLength),
			Mode:    0644,
			ModTime: aws.ToTime(head.LastModified),
			IsDir:   false,
			Meta: filesystem.MetaData{
				Name: PluginName,
				Type: "s3",
				Content: map[string]string{
					"region": fs.client.region,
					"bucket": fs.client.bucket,
					"prefix": fs.client.prefix,
				},
			},
		}
		fs.statCache.Put(path, info)
		return info, nil
	}

	// Try as directory
	dirExists, err := fs.client.DirectoryExists(ctx, path)
	if err != nil {
		return nil, fmt.Errorf("failed to check directory: %w", err)
	}

	if dirExists {
		info := &filesystem.FileInfo{
			Name:    filepath.Base(path),
			Size:    0,
			Mode:    0755,
			ModTime: time.Now(),
			IsDir:   true,
			Meta: filesystem.MetaData{
				Name: PluginName,
				Type: "s3",
				Content: map[string]string{
					"region": fs.client.region,
					"bucket": fs.client.bucket,
					"prefix": fs.client.prefix,
				},
			},
		}
		fs.statCache.Put(path, info)
		return info, nil
	}

	return nil, filesystem.ErrNotFound
}

func (fs *S3FS) Rename(oldPath, newPath string) error {
	oldPath = filesystem.NormalizeS3Key(oldPath)
	newPath = filesystem.NormalizeS3Key(newPath)
	ctx := context.Background()

	fs.mu.Lock()
	defer fs.mu.Unlock()

	// Try as file first
	fileExists, err := fs.client.ObjectExists(ctx, oldPath)
	if err != nil {
		return fmt.Errorf("failed to check source: %w", err)
	}

	if fileExists {
		return fs.renameSingleObject(ctx, oldPath, newPath)
	}

	// Try as directory
	dirExists, err := fs.client.DirectoryExists(ctx, oldPath)
	if err != nil {
		return fmt.Errorf("failed to check source directory: %w", err)
	}
	if !dirExists {
		return filesystem.ErrNotFound
	}

	return fs.renameDirectory(ctx, oldPath, newPath)
}

// renameSingleObject moves a single S3 object via copy + delete.
func (fs *S3FS) renameSingleObject(ctx context.Context, oldPath, newPath string) error {
	if err := fs.client.CopyObject(ctx, oldPath, newPath); err != nil {
		return fmt.Errorf("failed to copy source: %w", err)
	}

	if err := fs.client.DeleteObject(ctx, oldPath); err != nil {
		return fmt.Errorf("failed to delete source: %w", err)
	}

	oldParent := getParentPath(oldPath)
	newParent := getParentPath(newPath)
	fs.dirCache.Invalidate(oldParent)
	fs.dirCache.Invalidate(newParent)
	fs.statCache.Invalidate(oldPath)
	fs.statCache.Invalidate(newPath)

	return nil
}

// renameDirectory moves an entire directory subtree by copying every object
// under oldPath to newPath and then deleting the originals.
func (fs *S3FS) renameDirectory(ctx context.Context, oldPath, newPath string) error {
	// List every object (recursively) under oldPath
	objects, err := fs.client.ListAllObjects(ctx, oldPath)
	if err != nil {
		return fmt.Errorf("failed to list source directory: %w", err)
	}

	// Copy each object to the new prefix
	for _, obj := range objects {
		srcRel := obj.Key // relative to oldPath
		if err := fs.client.CopyObject(ctx, oldPath+"/"+srcRel, newPath+"/"+srcRel); err != nil {
			return fmt.Errorf("failed to copy %s: %w", srcRel, err)
		}
	}

	// Create the new directory marker
	if err := fs.client.CreateDirectory(ctx, newPath); err != nil {
		// Ignore if already exists (implicit from copied children)
		log.Debugf("[s3fs] CreateDirectory %s (may already exist): %v", newPath, err)
	}

	// Delete old directory tree (marker + all children)
	if err := fs.client.DeleteDirectory(ctx, oldPath); err != nil {
		return fmt.Errorf("failed to delete source directory: %w", err)
	}

	// Invalidate caches broadly
	oldParent := getParentPath(oldPath)
	newParent := getParentPath(newPath)
	fs.dirCache.Invalidate(oldParent)
	fs.dirCache.Invalidate(newParent)
	fs.dirCache.InvalidatePrefix(oldPath)
	fs.dirCache.InvalidatePrefix(newPath)
	fs.statCache.InvalidatePrefix(oldPath)
	fs.statCache.InvalidatePrefix(newPath)

	return nil
}

func (fs *S3FS) Chmod(path string, mode uint32) error {
	// S3 doesn't support Unix permissions
	// This is a no-op for compatibility
	return nil
}

func (fs *S3FS) Open(path string) (io.ReadCloser, error) {
	data, err := fs.Read(path, 0, -1)
	if err != nil && err != io.EOF {
		return nil, err
	}
	return io.NopCloser(strings.NewReader(string(data))), nil
}

func (fs *S3FS) OpenWrite(path string) (io.WriteCloser, error) {
	return &s3fsWriter{fs: fs, path: path}, nil
}

type s3fsWriter struct {
	fs   *S3FS
	path string
	buf  []byte
}

func (w *s3fsWriter) Write(p []byte) (n int, err error) {
	w.buf = append(w.buf, p...)
	return len(p), nil
}

func (w *s3fsWriter) Close() error {
	_, err := w.fs.Write(w.path, w.buf, -1, filesystem.WriteFlagCreate|filesystem.WriteFlagTruncate)
	return err
}

// S3FSPlugin wraps S3FS as a plugin
type S3FSPlugin struct {
	fs     *S3FS
	config map[string]interface{}
}

// NewS3FSPlugin creates a new S3FS plugin
func NewS3FSPlugin() *S3FSPlugin {
	return &S3FSPlugin{}
}

func (p *S3FSPlugin) Name() string {
	return PluginName
}

func (p *S3FSPlugin) Validate(cfg map[string]interface{}) error {
	// Check for unknown parameters
	allowedKeys := []string{
		"bucket", "region", "access_key_id", "secret_access_key", "endpoint", "prefix", "disable_ssl", "mount_path",
		"cache_enabled", "cache_ttl", "stat_cache_ttl", "cache_max_size", "use_path_style",
	}
	if err := config.ValidateOnlyKnownKeys(cfg, allowedKeys); err != nil {
		return err
	}

	// Validate bucket (required)
	if _, err := config.RequireString(cfg, "bucket"); err != nil {
		return err
	}

	// Validate optional string parameters
	for _, key := range []string{"region", "access_key_id", "secret_access_key", "endpoint", "prefix"} {
		if err := config.ValidateStringType(cfg, key); err != nil {
			return err
		}
	}

	// Validate disable_ssl (optional boolean)
	if err := config.ValidateBoolType(cfg, "disable_ssl"); err != nil {
		return err
	}

	// Validate use_path_style (optional boolean)
	if err := config.ValidateBoolType(cfg, "use_path_style"); err != nil {
		return err
	}

	// Validate cache_enabled (optional boolean)
	if err := config.ValidateBoolType(cfg, "cache_enabled"); err != nil {
		return err
	}

	return nil
}

func (p *S3FSPlugin) Initialize(config map[string]interface{}) error {
	p.config = config

	// Parse S3 configuration
	cfg := S3Config{
		Region:          getStringConfig(config, "region", "us-east-1"),
		Bucket:          getStringConfig(config, "bucket", ""),
		AccessKeyID:     getStringConfig(config, "access_key_id", ""),
		SecretAccessKey: getStringConfig(config, "secret_access_key", ""),
		Endpoint:        getStringConfig(config, "endpoint", ""),
		Prefix:          getStringConfig(config, "prefix", ""),
		DisableSSL:      getBoolConfig(config, "disable_ssl", false),
		UsePathStyle:    getBoolConfig(config, "use_path_style", true),
	}

	if cfg.Bucket == "" {
		return fmt.Errorf("bucket name is required")
	}

	// Parse cache configuration
	cacheCfg := CacheConfig{
		Enabled:      getBoolConfig(config, "cache_enabled", true),
		DirCacheTTL:  getDurationConfig(config, "cache_ttl", 30*time.Second),
		StatCacheTTL: getDurationConfig(config, "stat_cache_ttl", 60*time.Second),
		MaxSize:      getIntConfig(config, "cache_max_size", 1000),
	}

	// Create S3FS instance with cache
	fs, err := NewS3FSWithCache(cfg, cacheCfg)
	if err != nil {
		return fmt.Errorf("failed to initialize s3fs: %w", err)
	}
	p.fs = fs

	log.Infof("[s3fs] Initialized with bucket: %s, region: %s, cache: %v", cfg.Bucket, cfg.Region, cacheCfg.Enabled)
	return nil
}

func (p *S3FSPlugin) GetFileSystem() filesystem.FileSystem {
	return p.fs
}

func (p *S3FSPlugin) GetReadme() string {
	return getReadme()
}

func (p *S3FSPlugin) GetConfigParams() []plugin.ConfigParameter {
	return []plugin.ConfigParameter{
		{
			Name:        "bucket",
			Type:        "string",
			Required:    true,
			Default:     "",
			Description: "S3 bucket name",
		},
		{
			Name:        "region",
			Type:        "string",
			Required:    false,
			Default:     "us-east-1",
			Description: "AWS region",
		},
		{
			Name:        "access_key_id",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "AWS access key ID (uses env AWS_ACCESS_KEY_ID if not provided)",
		},
		{
			Name:        "secret_access_key",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "AWS secret access key (uses env AWS_SECRET_ACCESS_KEY if not provided)",
		},
		{
			Name:        "endpoint",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "Custom S3 endpoint for S3-compatible services (e.g., MinIO)",
		},
		{
			Name:        "prefix",
			Type:        "string",
			Required:    false,
			Default:     "",
			Description: "Key prefix for namespace isolation",
		},
		{
			Name:        "disable_ssl",
			Type:        "bool",
			Required:    false,
			Default:     "false",
			Description: "Disable SSL for S3 connections",
		},
		{
			Name:        "use_path_style",
			Type:        "bool",
			Required:    false,
			Default:     "true",
			Description: "Whether to use path-style addressing (true) or virtual-host-style (false). Defaults to false for TOS, true for other services.",
		},
		{
			Name:        "cache_enabled",
			Type:        "bool",
			Required:    false,
			Default:     "true",
			Description: "Enable caching for directory listings and stat results",
		},
		{
			Name:        "cache_ttl",
			Type:        "string",
			Required:    false,
			Default:     "30s",
			Description: "TTL for directory listing cache (e.g., '30s', '1m')",
		},
		{
			Name:        "stat_cache_ttl",
			Type:        "string",
			Required:    false,
			Default:     "60s",
			Description: "TTL for stat result cache (e.g., '60s', '2m')",
		},
		{
			Name:        "cache_max_size",
			Type:        "int",
			Required:    false,
			Default:     "1000",
			Description: "Maximum number of entries in each cache",
		},
	}
}

func (p *S3FSPlugin) Shutdown() error {
	return nil
}

func getReadme() string {
	return `S3FS Plugin - AWS S3-backed File System

This plugin provides a file system backed by AWS S3 object storage.

FEATURES:
  - Store files and directories in AWS S3
  - Support for S3-compatible services (MinIO, LocalStack, etc.)
  - Full POSIX-like file system operations
  - Streaming support for efficient large file handling
  - Automatic directory handling
  - Optional key prefix for namespace isolation

CONFIGURATION:

  AWS S3:
  [plugins.s3fs]
  enabled = true
  path = "/s3fs"

    [plugins.s3fs.config]
    region = "us-east-1"
    bucket = "my-bucket"
    access_key_id = "AKIAIOSFODNN7EXAMPLE"
    secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    prefix = "agfs/"  # Optional: all keys will be prefixed with this

  S3-Compatible Service (MinIO, LocalStack):
  [plugins.s3fs]
  enabled = true
  path = "/s3fs"

    [plugins.s3fs.config]
    region = "us-east-1"
    bucket = "my-bucket"
    access_key_id = "minioadmin"
    secret_access_key = "minioadmin"
    endpoint = "http://localhost:9000"
    disable_ssl = true

  Multiple S3 Buckets:
  [plugins.s3fs_prod]
  enabled = true
  path = "/s3/prod"

    [plugins.s3fs_prod.config]
    region = "us-east-1"
    bucket = "production-bucket"
    access_key_id = "..."
    secret_access_key = "..."

  [plugins.s3fs_dev]
  enabled = true
  path = "/s3/dev"

    [plugins.s3fs_dev.config]
    region = "us-west-2"
    bucket = "development-bucket"
    access_key_id = "..."
    secret_access_key = "..."

USAGE:

  Create a directory:
    agfs mkdir /s3fs/data

  Create a file:
    agfs write /s3fs/data/file.txt "Hello, S3!"

  Read a file:
    agfs cat /s3fs/data/file.txt

  Stream a large file (memory efficient):
    agfs cat --stream /s3fs/data/large-video.mp4 > output.mp4

  List directory:
    agfs ls /s3fs/data

  Remove file:
    agfs rm /s3fs/data/file.txt

  Remove directory (must be empty):
    agfs rm /s3fs/data

  Remove directory recursively:
    agfs rm -r /s3fs/data

EXAMPLES:

  # Basic file operations
  agfs:/> mkdir /s3fs/documents
  agfs:/> echo "Important data" > /s3fs/documents/report.txt
  agfs:/> cat /s3fs/documents/report.txt
  Important data

  # List contents
  agfs:/> ls /s3fs/documents
  report.txt

  # Move/rename
  agfs:/> mv /s3fs/documents/report.txt /s3fs/documents/report-2024.txt

  # Stream large files efficiently
  agfs:/> cat --stream /s3fs/videos/movie.mp4 > local-movie.mp4
  # Streams in 256KB chunks, minimal memory usage

NOTES:
  - S3 doesn't have real directories; they are simulated with "/" in object keys
  - Use --stream flag for large files to minimize memory usage (256KB chunks)
  - Permissions (chmod) are not supported by S3
  - Atomic operations are limited by S3's eventual consistency model
  - Streaming is automatically used when accessing via Python SDK with stream=True

USE CASES:
  - Cloud-native file storage
  - Backup and archival
  - Sharing files across distributed systems
  - Cost-effective long-term storage
  - Integration with AWS services

ADVANTAGES:
  - Unlimited storage capacity
  - High durability (99.999999999%)
  - Geographic redundancy
  - Pay-per-use pricing
  - Efficient streaming for large files with minimal memory footprint
  - Versioning and lifecycle policies (via S3 bucket settings)
`
}

// Helper functions
func getStringConfig(config map[string]interface{}, key, defaultValue string) string {
	if val, ok := config[key].(string); ok && val != "" {
		return val
	}
	return defaultValue
}

func getBoolConfig(config map[string]interface{}, key string, defaultValue bool) bool {
	if val, ok := config[key].(bool); ok {
		return val
	}
	return defaultValue
}

func getIntConfig(config map[string]interface{}, key string, defaultValue int) int {
	if val, ok := config[key].(int); ok {
		return val
	}
	if val, ok := config[key].(float64); ok {
		return int(val)
	}
	return defaultValue
}

func getDurationConfig(config map[string]interface{}, key string, defaultValue time.Duration) time.Duration {
	// Try string format like "30s", "1m", "1h"
	if val, ok := config[key].(string); ok && val != "" {
		if d, err := time.ParseDuration(val); err == nil {
			return d
		}
	}
	// Try numeric (seconds)
	if val, ok := config[key].(int); ok {
		return time.Duration(val) * time.Second
	}
	if val, ok := config[key].(float64); ok {
		return time.Duration(val) * time.Second
	}
	return defaultValue
}

// s3StreamReader implements filesystem.StreamReader for S3 objects
type s3StreamReader struct {
	body      io.ReadCloser
	chunkSize int64
	closed    bool
	mu        sync.Mutex
}

// ReadChunk reads the next chunk from the S3 object stream
func (r *s3StreamReader) ReadChunk(timeout time.Duration) ([]byte, bool, error) {
	r.mu.Lock()
	defer r.mu.Unlock()

	if r.closed {
		return nil, true, io.EOF
	}

	// Create context with timeout
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	// Prepare buffer for reading
	buf := make([]byte, r.chunkSize)

	// Channel to receive read result
	type readResult struct {
		n   int
		err error
	}
	resultCh := make(chan readResult, 1)

	// Read in goroutine to support timeout
	go func() {
		n, err := r.body.Read(buf)
		resultCh <- readResult{n: n, err: err}
	}()

	// Wait for read or timeout
	select {
	case result := <-resultCh:
		if result.err == io.EOF {
			// End of file reached
			if result.n > 0 {
				return buf[:result.n], true, nil
			}
			return nil, true, io.EOF
		}
		if result.err != nil {
			return nil, false, result.err
		}
		return buf[:result.n], false, nil

	case <-ctx.Done():
		// Timeout occurred
		return nil, false, fmt.Errorf("read timeout: %w", ctx.Err())
	}
}

// Close closes the S3 object stream
func (r *s3StreamReader) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()

	if r.closed {
		return nil
	}

	r.closed = true
	return r.body.Close()
}

// OpenStream opens a stream for reading an S3 object
// This implements the filesystem.Streamer interface
func (fs *S3FS) OpenStream(path string) (filesystem.StreamReader, error) {
	path = filesystem.NormalizeS3Key(path)
	ctx := context.Background()

	fs.mu.RLock()
	defer fs.mu.RUnlock()

	// Get streaming reader from S3
	body, err := fs.client.GetObjectStream(ctx, path)
	if err != nil {
		if strings.Contains(err.Error(), "NoSuchKey") || strings.Contains(err.Error(), "NotFound") {
			return nil, filesystem.ErrNotFound
		}
		return nil, err
	}

	// Create stream reader with 256KB chunk size (balanced for S3)
	return &s3StreamReader{
		body:      body,
		chunkSize: 256 * 1024, // 256KB chunks
		closed:    false,
	}, nil
}

// Ensure S3FSPlugin implements ServicePlugin
var _ plugin.ServicePlugin = (*S3FSPlugin)(nil)
var _ filesystem.FileSystem = (*S3FS)(nil)
var _ filesystem.Streamer = (*S3FS)(nil)
