package filesystem

import (
	"path"
	"strings"
)

// NormalizePath normalizes a filesystem path to a canonical form.
// - Empty paths and "/" return "/"
// - Adds leading "/" if missing
// - Cleans the path (removes .., ., etc.)
// - Removes trailing slashes (except for root "/")
//
// This is used by most filesystem implementations (memfs, sqlfs, httpfs, etc.)
func NormalizePath(p string) string {
	if p == "" || p == "/" {
		return "/"
	}

	// Ensure leading slash
	if !strings.HasPrefix(p, "/") {
		p = "/" + p
	}

	// Clean the path (resolve .., ., etc.)
	// Use path.Clean instead of filepath.Clean to ensure consistency across OS
	// and always use forward slashes for VFS paths
	p = path.Clean(p)

	// path.Clean can return "." for some inputs
	if p == "." {
		return "/"
	}

	// Remove trailing slash (Clean might leave it in some cases)
	if len(p) > 1 && strings.HasSuffix(p, "/") {
		p = p[:len(p)-1]
	}

	return p
}

// NormalizeS3Key normalizes an S3 object key.
// S3 keys don't have a leading slash, so this:
// - Returns "" for empty paths or "/"
// - Removes leading "/"
// - Cleans the path
//
// This is used specifically by s3fs plugin.
func NormalizeS3Key(p string) string {
	if p == "" || p == "/" {
		return ""
	}

	// Remove leading slash (S3 keys don't have them)
	p = strings.TrimPrefix(p, "/")

	// Clean the path
	// Use path.Clean instead of filepath.Clean
	p = path.Clean(p)

	// path.Clean returns "." for empty/root paths
	if p == "." {
		return ""
	}

	return p
}
