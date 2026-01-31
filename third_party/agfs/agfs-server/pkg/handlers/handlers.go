package handlers

import (
	"bufio"
	"bytes"
	"crypto/md5"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"path"
	"regexp"
	"strconv"
	"time"

	"github.com/c4pt0r/agfs/agfs-server/pkg/filesystem"
	log "github.com/sirupsen/logrus"
	"github.com/zeebo/xxh3"
)

// Handler wraps the FileSystem and provides HTTP handlers
type Handler struct {
	fs             filesystem.FileSystem
	version        string
	gitCommit      string
	buildTime      string
	trafficMonitor *TrafficMonitor
}

// NewHandler creates a new Handler
func NewHandler(fs filesystem.FileSystem, trafficMonitor *TrafficMonitor) *Handler {
	return &Handler{
		fs:             fs,
		version:        "dev",
		gitCommit:      "unknown",
		buildTime:      "unknown",
		trafficMonitor: trafficMonitor,
	}
}

// SetVersionInfo sets the version information for the handler
func (h *Handler) SetVersionInfo(version, gitCommit, buildTime string) {
	h.version = version
	h.gitCommit = gitCommit
	h.buildTime = buildTime
}

// ErrorResponse represents an error response
type ErrorResponse struct {
	Error string `json:"error"`
}

// SuccessResponse represents a success response
type SuccessResponse struct {
	Message string `json:"message"`
}

// FileInfoResponse represents file info response
type FileInfoResponse struct {
	Name    string              `json:"name"`
	Size    int64               `json:"size"`
	Mode    uint32              `json:"mode"`
	ModTime string              `json:"modTime"`
	IsDir   bool                `json:"isDir"`
	Meta    filesystem.MetaData `json:"meta,omitempty"` // Structured metadata
}

// ListResponse represents directory listing response
type ListResponse struct {
	Files []FileInfoResponse `json:"files"`
}

// WriteRequest represents a write request
type WriteRequest struct {
	Data string `json:"data"`
}

// RenameRequest represents a rename request
type RenameRequest struct {
	NewPath string `json:"newPath"`
}

// ChmodRequest represents a chmod request
type ChmodRequest struct {
	Mode uint32 `json:"mode"`
}

// DigestRequest represents a digest request
type DigestRequest struct {
	Algorithm string `json:"algorithm"` // "xxh3" or "md5"
	Path      string `json:"path"`      // Path to the file
}

// DigestResponse represents the digest result
type DigestResponse struct {
	Algorithm string `json:"algorithm"` // Algorithm used
	Path      string `json:"path"`      // File path
	Digest    string `json:"digest"`    // Hex-encoded digest
}

func writeJSON(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data)
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, ErrorResponse{Error: message})
}

// mapErrorToStatus maps filesystem errors to HTTP status codes
func mapErrorToStatus(err error) int {
	if errors.Is(err, filesystem.ErrNotFound) {
		return http.StatusNotFound
	}
	if errors.Is(err, filesystem.ErrPermissionDenied) {
		return http.StatusForbidden
	}
	if errors.Is(err, filesystem.ErrInvalidArgument) {
		return http.StatusBadRequest
	}
	if errors.Is(err, filesystem.ErrAlreadyExists) {
		return http.StatusConflict
	}
	if errors.Is(err, filesystem.ErrNotSupported) {
		return http.StatusNotImplemented
	}
	return http.StatusInternalServerError
}

// CreateFile handles POST /files?path=<path>
func (h *Handler) CreateFile(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if path == "" {
		writeError(w, http.StatusBadRequest, "path parameter is required")
		return
	}

	if err := h.fs.Create(path); err != nil {
		status := mapErrorToStatus(err)
		writeError(w, status, err.Error())
		return
	}

	writeJSON(w, http.StatusCreated, SuccessResponse{Message: "file created"})
}

// CreateDirectory handles POST /directories?path=<path>&mode=<mode>
func (h *Handler) CreateDirectory(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if path == "" {
		writeError(w, http.StatusBadRequest, "path parameter is required")
		return
	}

	modeStr := r.URL.Query().Get("mode")
	mode := uint32(0755)
	if modeStr != "" {
		m, err := strconv.ParseUint(modeStr, 8, 32)
		if err != nil {
			writeError(w, http.StatusBadRequest, "invalid mode")
			return
		}
		mode = uint32(m)
	}

	if err := h.fs.Mkdir(path, mode); err != nil {
		status := mapErrorToStatus(err)
		writeError(w, status, err.Error())
		return
	}

	writeJSON(w, http.StatusCreated, SuccessResponse{Message: "directory created"})
}

// ReadFile handles GET /files?path=<path>&offset=<offset>&size=<size>&stream=<true|false>
func (h *Handler) ReadFile(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if path == "" {
		writeError(w, http.StatusBadRequest, "path parameter is required")
		return
	}

	// Check if streaming mode is requested
	stream := r.URL.Query().Get("stream") == "true"
	if stream {
		h.streamFile(w, r, path)
		return
	}

	// Parse offset and size parameters
	offset := int64(0)
	size := int64(-1) // -1 means read all

	if offsetStr := r.URL.Query().Get("offset"); offsetStr != "" {
		if parsedOffset, err := strconv.ParseInt(offsetStr, 10, 64); err == nil {
			offset = parsedOffset
		} else {
			writeError(w, http.StatusBadRequest, "invalid offset parameter")
			return
		}
	}

	if sizeStr := r.URL.Query().Get("size"); sizeStr != "" {
		if parsedSize, err := strconv.ParseInt(sizeStr, 10, 64); err == nil {
			size = parsedSize
		} else {
			writeError(w, http.StatusBadRequest, "invalid size parameter")
			return
		}
	}

	data, err := h.fs.Read(path, offset, size)
	if err != nil {
		// Check if it's EOF (reached end of file)
		if err == io.EOF {
			w.Header().Set("Content-Type", "application/octet-stream")
			w.WriteHeader(http.StatusOK)
			w.Write(data) // Return partial data with 200 OK
			// Record downstream traffic
			if h.trafficMonitor != nil && len(data) > 0 {
				h.trafficMonitor.RecordRead(int64(len(data)))
			}
			return
		}
		// Map error to appropriate HTTP status code
		status := mapErrorToStatus(err)
		writeError(w, status, err.Error())
		return
	}

	w.Header().Set("Content-Type", "application/octet-stream")
	w.WriteHeader(http.StatusOK)
	w.Write(data)

	// Record downstream traffic
	if h.trafficMonitor != nil && len(data) > 0 {
		h.trafficMonitor.RecordRead(int64(len(data)))
	}
}

// WriteFile handles PUT /files?path=<path>
func (h *Handler) WriteFile(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if path == "" {
		writeError(w, http.StatusBadRequest, "path parameter is required")
		return
	}

	data, err := io.ReadAll(r.Body)
	if err != nil {
		writeError(w, http.StatusBadRequest, "failed to read request body")
		return
	}

	// Record upstream traffic
	if h.trafficMonitor != nil && len(data) > 0 {
		h.trafficMonitor.RecordWrite(int64(len(data)))
	}

	// Use default flags: create if not exists, truncate (like the old behavior)
	bytesWritten, err := h.fs.Write(path, data, -1, filesystem.WriteFlagCreate|filesystem.WriteFlagTruncate)
	if err != nil {
		status := mapErrorToStatus(err)
		writeError(w, status, err.Error())
		return
	}

	// Return success with bytes written
	writeJSON(w, http.StatusOK, SuccessResponse{Message: fmt.Sprintf("Written %d bytes", bytesWritten)})
}

// Delete handles DELETE /files?path=<path>&recursive=<true|false>
func (h *Handler) Delete(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if path == "" {
		writeError(w, http.StatusBadRequest, "path parameter is required")
		return
	}

	recursive := r.URL.Query().Get("recursive") == "true"

	var err error
	if recursive {
		err = h.fs.RemoveAll(path)
	} else {
		err = h.fs.Remove(path)
	}

	if err != nil {
		status := mapErrorToStatus(err)
		writeError(w, status, err.Error())
		return
	}

	writeJSON(w, http.StatusOK, SuccessResponse{Message: "deleted"})
}

// ListDirectory handles GET /directories?path=<path>
func (h *Handler) ListDirectory(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if path == "" {
		path = "/"
	}

	files, err := h.fs.ReadDir(path)
	if err != nil {
		// Map error to appropriate HTTP status code
		status := mapErrorToStatus(err)
		writeError(w, status, err.Error())
		return
	}

	var response ListResponse
	for _, f := range files {
		response.Files = append(response.Files, FileInfoResponse{
			Name:    f.Name,
			Size:    f.Size,
			Mode:    f.Mode,
			ModTime: f.ModTime.Format(time.RFC3339Nano),
			IsDir:   f.IsDir,
			Meta:    f.Meta,
		})
	}

	writeJSON(w, http.StatusOK, response)
}

// Stat handles GET /stat?path=<path>
func (h *Handler) Stat(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if path == "" {
		writeError(w, http.StatusBadRequest, "path parameter is required")
		return
	}

	info, err := h.fs.Stat(path)
	if err != nil {
		status := mapErrorToStatus(err)
		// "Not found" is expected during cp/mv operations, use debug level
		if status == http.StatusNotFound {
			log.Debugf("Stat: path not found: %s (from %s)", path, r.RemoteAddr)
		} else {
			log.Errorf("Stat error for path %s: %v (from %s)", path, err, r.RemoteAddr)
		}
		writeError(w, status, err.Error())
		return
	}

	response := FileInfoResponse{
		Name:    info.Name,
		Size:    info.Size,
		Mode:    info.Mode,
		ModTime: info.ModTime.Format(time.RFC3339Nano),
		IsDir:   info.IsDir,
		Meta:    info.Meta,
	}

	writeJSON(w, http.StatusOK, response)
}

// Rename handles POST /rename?path=<path>
func (h *Handler) Rename(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if path == "" {
		writeError(w, http.StatusBadRequest, "path parameter is required")
		return
	}

	var req RenameRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if req.NewPath == "" {
		writeError(w, http.StatusBadRequest, "newPath is required")
		return
	}

	if err := h.fs.Rename(path, req.NewPath); err != nil {
		status := mapErrorToStatus(err)
		writeError(w, status, err.Error())
		return
	}

	writeJSON(w, http.StatusOK, SuccessResponse{Message: "renamed"})
}

// Chmod handles POST /chmod?path=<path>
func (h *Handler) Chmod(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if path == "" {
		writeError(w, http.StatusBadRequest, "path parameter is required")
		return
	}

	var req ChmodRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if err := h.fs.Chmod(path, req.Mode); err != nil {
		status := mapErrorToStatus(err)
		writeError(w, status, err.Error())
		return
	}

	writeJSON(w, http.StatusOK, SuccessResponse{Message: "permissions changed"})
}

// Digest handles POST /digest
func (h *Handler) Digest(w http.ResponseWriter, r *http.Request) {
	var req DigestRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body: "+err.Error())
		return
	}

	// Validate algorithm
	if req.Algorithm != "xxh3" && req.Algorithm != "md5" {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("unsupported algorithm: %s (supported: xxh3, md5)", req.Algorithm))
		return
	}

	// Validate path
	if req.Path == "" {
		writeError(w, http.StatusBadRequest, "path is required")
		return
	}

	// Calculate digest using streaming approach to handle large files
	var digest string
	var err error

	switch req.Algorithm {
	case "xxh3":
		digest, err = h.calculateXXH3Digest(req.Path)
	case "md5":
		digest, err = h.calculateMD5Digest(req.Path)
	default:
		writeError(w, http.StatusBadRequest, "unsupported algorithm: "+req.Algorithm)
		return
	}

	if err != nil {
		status := mapErrorToStatus(err)
		writeError(w, status, "failed to calculate digest: "+err.Error())
		return
	}

	response := DigestResponse{
		Algorithm: req.Algorithm,
		Path:      req.Path,
		Digest:    digest,
	}

	writeJSON(w, http.StatusOK, response)
}

// calculateXXH3Digest calculates XXH3 hash using streaming approach
func (h *Handler) calculateXXH3Digest(path string) (string, error) {
	// Try to open file for streaming
	reader, err := h.fs.Open(path)
	if err != nil {
		return "", err
	}
	defer reader.Close()

	// Stream and hash the file in chunks
	hasher := xxh3.New()
	buffer := make([]byte, 64*1024) // 64KB buffer

	for {
		n, err := reader.Read(buffer)
		if n > 0 {
			hasher.Write(buffer[:n])
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", fmt.Errorf("error reading file: %w", err)
		}
	}

	hash := hasher.Sum128().Lo // Use lower 64 bits for consistency
	return fmt.Sprintf("%016x", hash), nil
}

// calculateMD5Digest calculates MD5 hash using streaming approach
func (h *Handler) calculateMD5Digest(path string) (string, error) {
	// Try to open file for streaming
	reader, err := h.fs.Open(path)
	if err != nil {
		return "", err
	}
	defer reader.Close()

	// Stream and hash the file in chunks
	hasher := md5.New()
	buffer := make([]byte, 64*1024) // 64KB buffer

	for {
		n, err := reader.Read(buffer)
		if n > 0 {
			hasher.Write(buffer[:n])
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", fmt.Errorf("error reading file: %w", err)
		}
	}

	return hex.EncodeToString(hasher.Sum(nil)), nil
}

// CapabilitiesResponse represents the server capabilities
type CapabilitiesResponse struct {
	Version  string   `json:"version"`
	Features []string `json:"features"`
}

// Capabilities handles GET /capabilities
func (h *Handler) Capabilities(w http.ResponseWriter, r *http.Request) {
	response := CapabilitiesResponse{
		Version: h.version,
		Features: []string{
			"handlefs", // File handles for stateful operations
			"grep",     // Server-side grep
			"digest",   // Server-side checksums
			"stream",   // Streaming read
			"touch",    // Touch/update timestamp
		},
	}
	writeJSON(w, http.StatusOK, response)
}

// HealthResponse represents the health check response
type HealthResponse struct {
	Status    string `json:"status"`
	Version   string `json:"version"`
	GitCommit string `json:"gitCommit"`
	BuildTime string `json:"buildTime"`
}

// Health handles GET /health
func (h *Handler) Health(w http.ResponseWriter, r *http.Request) {
	response := HealthResponse{
		Status:    "healthy",
		Version:   h.version,
		GitCommit: h.gitCommit,
		BuildTime: h.buildTime,
	}
	writeJSON(w, http.StatusOK, response)
}

// Touch handles POST /touch?path=<path>
// Updates file timestamp without changing content
// If file doesn't exist, creates it with empty content
func (h *Handler) Touch(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if path == "" {
		writeError(w, http.StatusBadRequest, "path parameter is required")
		return
	}

	// Check if filesystem implements efficient Touch
	if toucher, ok := h.fs.(filesystem.Toucher); ok {
		// Use efficient touch implementation
		err := toucher.Touch(path)
		if err != nil {
			status := mapErrorToStatus(err)
			writeError(w, status, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, SuccessResponse{Message: "touched"})
		return
	}

	// Fallback: inefficient implementation for filesystems without Touch
	// Check if file exists
	info, err := h.fs.Stat(path)
	if err == nil {
		// File exists - read current content and write it back to update timestamp
		if !info.IsDir {
			data, readErr := h.fs.Read(path, 0, -1)
			if readErr != nil {
				status := mapErrorToStatus(readErr)
				writeError(w, status, readErr.Error())
				return
			}
			_, writeErr := h.fs.Write(path, data, -1, filesystem.WriteFlagTruncate)
			if writeErr != nil {
				status := mapErrorToStatus(writeErr)
				writeError(w, status, writeErr.Error())
				return
			}
		} else {
			// Can't touch a directory
			writeError(w, http.StatusBadRequest, "cannot touch directory")
			return
		}
	} else {
		// File doesn't exist - create with empty content
		_, err := h.fs.Write(path, []byte{}, -1, filesystem.WriteFlagCreate)
		if err != nil {
			status := mapErrorToStatus(err)
			writeError(w, status, err.Error())
			return
		}
	}

	writeJSON(w, http.StatusOK, SuccessResponse{Message: "touched"})
}

// SetupRoutes sets up all HTTP routes with /api/v1 prefix
func (h *Handler) SetupRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/api/v1/health", h.Health)
	mux.HandleFunc("/api/v1/capabilities", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		h.Capabilities(w, r)
	})

	// Setup handle routes (file handles for stateful operations)
	h.SetupHandleRoutes(mux)

	mux.HandleFunc("/api/v1/files", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodPost:
			h.CreateFile(w, r)
		case http.MethodGet:
			h.ReadFile(w, r)
		case http.MethodPut:
			h.WriteFile(w, r)
		case http.MethodDelete:
			h.Delete(w, r)
		default:
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		}
	})
	mux.HandleFunc("/api/v1/directories", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodPost:
			h.CreateDirectory(w, r)
		case http.MethodGet:
			h.ListDirectory(w, r)
		case http.MethodDelete:
			h.Delete(w, r)
		default:
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		}
	})
	mux.HandleFunc("/api/v1/stat", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		h.Stat(w, r)
	})
	mux.HandleFunc("/api/v1/rename", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		h.Rename(w, r)
	})
	mux.HandleFunc("/api/v1/chmod", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		h.Chmod(w, r)
	})
	mux.HandleFunc("/api/v1/grep", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		h.Grep(w, r)
	})
	mux.HandleFunc("/api/v1/digest", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		h.Digest(w, r)
	})
	mux.HandleFunc("/api/v1/touch", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		h.Touch(w, r)
	})
}

// streamFile handles streaming file reads with HTTP chunked transfer encoding
func (h *Handler) streamFile(w http.ResponseWriter, r *http.Request, path string) {
	// Check if filesystem supports streaming
	streamer, ok := h.fs.(filesystem.Streamer)
	if !ok {
		writeError(w, http.StatusBadRequest, "streaming not supported for this filesystem")
		return
	}

	// Open stream for reading
	reader, err := streamer.OpenStream(path)
	if err != nil {
		writeError(w, http.StatusNotFound, err.Error())
		return
	}
	defer reader.Close()

	// Stream data to client
	h.streamFromStreamReader(w, r, reader)
}

// streamFromStreamReader streams data from a filesystem.StreamReader using chunked transfer
func (h *Handler) streamFromStreamReader(w http.ResponseWriter, r *http.Request, reader filesystem.StreamReader) {
	// Set headers for chunked transfer
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Transfer-Encoding", "chunked")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(http.StatusOK)

	flusher, ok := w.(http.Flusher)
	if !ok {
		log.Error("ResponseWriter does not support flushing")
		return
	}

	log.Debugf("Starting stream read")

	// Read timeout for each chunk
	timeout := 30 * time.Second

	for {
		// Check if client disconnected
		select {
		case <-r.Context().Done():
			log.Infof("Client disconnected from stream")
			return
		default:
		}

		// Read next chunk from stream (blocking until data available)
		chunk, eof, err := reader.ReadChunk(timeout)

		if err != nil {
			if err == io.EOF {
				log.Infof("Stream closed (EOF)")
				return
			}
			if err.Error() == "read timeout" {
				// Timeout - stream is idle, continue waiting instead of closing
				log.Debugf("Stream read timeout, continuing to wait...")
				continue
			}
			log.Errorf("Error reading from stream: %v", err)
			return
		}

		if len(chunk) > 0 {
			// Write chunk to response in smaller pieces to avoid overwhelming the client
			maxChunkSize := 64 * 1024 // 64KB at a time
			offset := 0

			for offset < len(chunk) {
				// Check if client disconnected
				select {
				case <-r.Context().Done():
					log.Infof("Client disconnected while writing chunk")
					return
				default:
				}
				end := offset + maxChunkSize
				if end > len(chunk) {
					end = len(chunk)
				}
				n, writeErr := w.Write(chunk[offset:end])
				if writeErr != nil {
					log.Debugf("Error writing chunk: %v (this is normal if client disconnected)", writeErr)
					return
				}
				// Record downstream traffic
				if h.trafficMonitor != nil && n > 0 {
					h.trafficMonitor.RecordRead(int64(n))
				}
				offset += n
				// Flush after each piece
				flusher.Flush()
			}
		}
		if eof {
			log.Debug("Stream completed (EOF)")
			return
		}
	}
}

// GrepRequest represents a grep search request
type GrepRequest struct {
	Path            string `json:"path"`             // Path to file or directory to search
	Pattern         string `json:"pattern"`          // Regular expression pattern
	Recursive       bool   `json:"recursive"`        // Whether to search recursively in directories
	CaseInsensitive bool   `json:"case_insensitive"` // Case-insensitive matching
	Stream          bool   `json:"stream"`           // Stream results as NDJSON (one match per line)
}

// GrepMatch represents a single match result
type GrepMatch struct {
	File    string `json:"file"`    // File path
	Line    int    `json:"line"`    // Line number (1-indexed)
	Content string `json:"content"` // Matched line content
}

// GrepResponse represents the grep search results
type GrepResponse struct {
	Matches []GrepMatch `json:"matches"` // All matches
	Count   int         `json:"count"`   // Total number of matches
}

// Grep searches for a pattern in files
func (h *Handler) Grep(w http.ResponseWriter, r *http.Request) {
	var req GrepRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body: "+err.Error())
		return
	}

	// Validate request
	if req.Path == "" {
		writeError(w, http.StatusBadRequest, "path is required")
		return
	}
	if req.Pattern == "" {
		writeError(w, http.StatusBadRequest, "pattern is required")
		return
	}

	// Compile regex pattern
	var re *regexp.Regexp
	var err error
	if req.CaseInsensitive {
		re, err = regexp.Compile("(?i)" + req.Pattern)
	} else {
		re, err = regexp.Compile(req.Pattern)
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid regex pattern: "+err.Error())
		return
	}

	// Check if path exists and get file info
	info, err := h.fs.Stat(req.Path)
	if err != nil {
		status := mapErrorToStatus(err)
		writeError(w, status, "failed to stat path: "+err.Error())
		return
	}

	// Handle stream mode
	if req.Stream {
		h.grepStream(w, req.Path, re, info.IsDir, req.Recursive)
		return
	}

	// Non-stream mode: collect all matches
	var matches []GrepMatch

	// Search in file or directory
	if info.IsDir {
		if req.Recursive {
			matches, err = h.grepDirectory(req.Path, re)
		} else {
			writeError(w, http.StatusBadRequest, "path is a directory, use recursive=true to search")
			return
		}
	} else {
		matches, err = h.grepFile(req.Path, re)
	}

	if err != nil {
		writeError(w, http.StatusInternalServerError, "grep failed: "+err.Error())
		return
	}

	response := GrepResponse{
		Matches: matches,
		Count:   len(matches),
	}

	writeJSON(w, http.StatusOK, response)
}

// grepStream handles streaming grep results as NDJSON
func (h *Handler) grepStream(w http.ResponseWriter, path string, re *regexp.Regexp, isDir bool, recursive bool) {
	// Set headers for NDJSON streaming
	w.Header().Set("Content-Type", "application/x-ndjson")
	w.Header().Set("Transfer-Encoding", "chunked")
	w.WriteHeader(http.StatusOK)

	// Get flusher for chunked encoding
	flusher, ok := w.(http.Flusher)
	if !ok {
		log.Error("Streaming not supported")
		return
	}

	matchCount := 0
	encoder := json.NewEncoder(w)

	// Callback function to send each match
	sendMatch := func(match GrepMatch) error {
		matchCount++
		if err := encoder.Encode(match); err != nil {
			return err
		}
		flusher.Flush()
		return nil
	}

	// Search and stream results
	var err error
	if isDir {
		if !recursive {
			// Send error as JSON
			errMatch := map[string]interface{}{
				"error": "path is a directory, use recursive=true to search",
			}
			encoder.Encode(errMatch)
			flusher.Flush()
			return
		}
		err = h.grepDirectoryStream(path, re, sendMatch)
	} else {
		err = h.grepFileStream(path, re, sendMatch)
	}

	// Send final summary with count
	summary := map[string]interface{}{
		"type":  "summary",
		"count": matchCount,
	}
	if err != nil {
		summary["error"] = err.Error()
	}
	encoder.Encode(summary)
	flusher.Flush()
}

// grepFileStream searches for pattern in a single file and calls callback for each match
func (h *Handler) grepFileStream(path string, re *regexp.Regexp, callback func(GrepMatch) error) error {
	// Read file content
	data, err := h.fs.Read(path, 0, -1)
	// io.EOF is normal when reading entire file, only return error for other errors
	if err != nil && err != io.EOF {
		return err
	}

	scanner := bufio.NewScanner(bytes.NewReader(data))
	lineNum := 1

	for scanner.Scan() {
		line := scanner.Text()
		if re.MatchString(line) {
			match := GrepMatch{
				File:    path,
				Line:    lineNum,
				Content: line,
			}
			if err := callback(match); err != nil {
				return err
			}
		}
		lineNum++
	}

	if err := scanner.Err(); err != nil {
		return err
	}

	return nil
}

// grepDirectoryStream recursively searches for pattern in a directory and calls callback for each match
func (h *Handler) grepDirectoryStream(dirPath string, re *regexp.Regexp, callback func(GrepMatch) error) error {
	// List directory contents
	entries, err := h.fs.ReadDir(dirPath)
	if err != nil {
		return err
	}

	for _, entry := range entries {
		// Build full path
		// Use path.Join for VFS paths to ensure forward slashes on all OS
		fullPath := path.Join(dirPath, entry.Name)

		if entry.IsDir {
			// Recursively search subdirectories
			if err := h.grepDirectoryStream(fullPath, re, callback); err != nil {
				// Log error but continue searching other files
				log.Warnf("failed to search directory %s: %v", fullPath, err)
				continue
			}
		} else {
			// Search in file
			if err := h.grepFileStream(fullPath, re, callback); err != nil {
				// Log error but continue searching other files
				log.Warnf("failed to search file %s: %v", fullPath, err)
				continue
			}
		}
	}

	return nil
}

// grepFile searches for pattern in a single file
func (h *Handler) grepFile(path string, re *regexp.Regexp) ([]GrepMatch, error) {
	// Read file content
	data, err := h.fs.Read(path, 0, -1)
	// io.EOF is normal when reading entire file, only return error for other errors
	if err != nil && err != io.EOF {
		return nil, err
	}

	var matches []GrepMatch
	scanner := bufio.NewScanner(bytes.NewReader(data))
	lineNum := 1

	for scanner.Scan() {
		line := scanner.Text()
		if re.MatchString(line) {
			matches = append(matches, GrepMatch{
				File:    path,
				Line:    lineNum,
				Content: line,
			})
		}
		lineNum++
	}

	if err := scanner.Err(); err != nil {
		return nil, err
	}

	return matches, nil
}

// grepDirectory recursively searches for pattern in a directory
func (h *Handler) grepDirectory(dirPath string, re *regexp.Regexp) ([]GrepMatch, error) {
	var allMatches []GrepMatch

	// List directory contents
	entries, err := h.fs.ReadDir(dirPath)
	if err != nil {
		return nil, err
	}

	for _, entry := range entries {
		// Build full path
		// Use path.Join for VFS paths to ensure forward slashes on all OS
		fullPath := path.Join(dirPath, entry.Name)

		if entry.IsDir {
			// Recursively search subdirectories
			subMatches, err := h.grepDirectory(fullPath, re)
			if err != nil {
				// Log error but continue searching other files
				log.Warnf("failed to search directory %s: %v", fullPath, err)
				continue
			}
			allMatches = append(allMatches, subMatches...)
		} else {
			// Search in file
			matches, err := h.grepFile(fullPath, re)
			if err != nil {
				// Log error but continue searching other files
				log.Warnf("failed to search file %s: %v", fullPath, err)
				continue
			}
			allMatches = append(allMatches, matches...)
		}
	}

	return allMatches, nil
}

// LoggingMiddleware logs HTTP requests
func LoggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		if r.URL.RawQuery != "" {
			path += "?" + r.URL.RawQuery
		}
		log.Debugf("%s %s", r.Method, path)
		next.ServeHTTP(w, r)
	})
}
