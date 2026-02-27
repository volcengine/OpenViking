package s3fs

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"path/filepath"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	log "github.com/sirupsen/logrus"
)

// S3Client wraps AWS S3 client with helper methods
type S3Client struct {
	client *s3.Client
	bucket string
	region string // AWS region
	prefix string // Optional prefix for all keys
}

// S3Config holds S3 client configuration
type S3Config struct {
	Region          string
	Bucket          string
	AccessKeyID     string
	SecretAccessKey string
	Endpoint        string // Optional custom endpoint (for S3-compatible services)
	Prefix          string // Optional prefix for all keys
	DisableSSL      bool   // For testing with local S3
	UsePathStyle    bool  // Whether to use path-style addressing (true) or virtual-host-style (false)
}

// NewS3Client creates a new S3 client
func NewS3Client(cfg S3Config) (*S3Client, error) {
	ctx := context.Background()

	var awsCfg aws.Config
	var err error

	// Build AWS config options
	opts := []func(*config.LoadOptions) error{
		config.WithRegion(cfg.Region),
	}

	// Add credentials if provided
	if cfg.AccessKeyID != "" && cfg.SecretAccessKey != "" {
		opts = append(opts, config.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(cfg.AccessKeyID, cfg.SecretAccessKey, ""),
		))
	}

	awsCfg, err = config.LoadDefaultConfig(ctx, opts...)
	if err != nil {
		return nil, fmt.Errorf("failed to load AWS config: %w", err)
	}

	// Create S3 client options
	clientOpts := []func(*s3.Options){}

	// Set custom endpoint if provided (for MinIO, LocalStack, TOS, etc.)
	if cfg.Endpoint != "" {
		clientOpts = append(clientOpts, func(o *s3.Options) {
			o.BaseEndpoint = aws.String(cfg.Endpoint)
			// true represent UsePathStyle for MinIO and some S3-compatible services
			// false represent VirtualHostStyle for TOS  and some S3-compatible services
			o.UsePathStyle = cfg.UsePathStyle 
		})
	}

	client := s3.NewFromConfig(awsCfg, clientOpts...)

	// Verify bucket exists
	_, err = client.HeadBucket(ctx, &s3.HeadBucketInput{
		Bucket: aws.String(cfg.Bucket),
	})
	if err != nil {
		return nil, fmt.Errorf("failed to access bucket %s: %w", cfg.Bucket, err)
	}

	log.Infof("[s3fs] Connected to S3 bucket: %s (region: %s)", cfg.Bucket, cfg.Region)

	// Normalize prefix: remove leading and trailing slashes
	prefix := strings.Trim(cfg.Prefix, "/")

	return &S3Client{
		client: client,
		bucket: cfg.Bucket,
		region: cfg.Region,
		prefix: prefix,
	}, nil
}

// buildKey builds the full S3 key with prefix
func (c *S3Client) buildKey(path string) string {
	// Normalize path
	path = strings.TrimPrefix(path, "/")

	if c.prefix == "" {
		return path
	}

	if path == "" {
		return c.prefix
	}

	return c.prefix + "/" + path
}

// GetObject retrieves an object from S3
func (c *S3Client) GetObject(ctx context.Context, path string) ([]byte, error) {
	key := c.buildKey(path)

	result, err := c.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, fmt.Errorf("failed to get object %s: %w", key, err)
	}
	defer result.Body.Close()

	data, err := io.ReadAll(result.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read object body: %w", err)
	}

	return data, nil
}

// GetObjectStream retrieves an object from S3 and returns a stream reader
// The caller is responsible for closing the returned ReadCloser
func (c *S3Client) GetObjectStream(ctx context.Context, path string) (io.ReadCloser, error) {
	key := c.buildKey(path)

	result, err := c.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, fmt.Errorf("failed to get object %s: %w", key, err)
	}

	return result.Body, nil
}

// GetObjectRange retrieves a byte range from an S3 object
// offset: starting byte position (0-based)
// size: number of bytes to read (-1 for all remaining bytes from offset)
func (c *S3Client) GetObjectRange(ctx context.Context, path string, offset, size int64) ([]byte, error) {
	key := c.buildKey(path)

	// Build range header
	var rangeHeader string
	if size < 0 {
		// From offset to end
		rangeHeader = fmt.Sprintf("bytes=%d-", offset)
	} else {
		// Specific range
		rangeHeader = fmt.Sprintf("bytes=%d-%d", offset, offset+size-1)
	}

	result, err := c.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
		Range:  aws.String(rangeHeader),
	})
	if err != nil {
		return nil, fmt.Errorf("failed to get object range %s: %w", key, err)
	}
	defer result.Body.Close()

	data, err := io.ReadAll(result.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read object body: %w", err)
	}

	return data, nil
}

// PutObject uploads an object to S3
func (c *S3Client) PutObject(ctx context.Context, path string, data []byte) error {
	key := c.buildKey(path)

	_, err := c.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
		Body:   bytes.NewReader(data),
	})
	if err != nil {
		return fmt.Errorf("failed to put object %s: %w", key, err)
	}

	return nil
}

// DeleteObject deletes an object from S3
func (c *S3Client) DeleteObject(ctx context.Context, path string) error {
	key := c.buildKey(path)

	_, err := c.client.DeleteObject(ctx, &s3.DeleteObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return fmt.Errorf("failed to delete object %s: %w", key, err)
	}

	return nil
}

// HeadObject checks if an object exists and returns its metadata
func (c *S3Client) HeadObject(ctx context.Context, path string) (*s3.HeadObjectOutput, error) {
	key := c.buildKey(path)

	result, err := c.client.HeadObject(ctx, &s3.HeadObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, err
	}

	return result, nil
}

// S3Object represents an S3 object with metadata
type S3Object struct {
	Key          string
	Size         int64
	LastModified time.Time
	IsDir        bool
}

// ListObjects lists objects with a given prefix
func (c *S3Client) ListObjects(ctx context.Context, path string) ([]S3Object, error) {
	// Normalize path to use as prefix
	prefix := c.buildKey(path)
	if prefix != "" && !strings.HasSuffix(prefix, "/") {
		prefix += "/"
	}

	var objects []S3Object
	paginator := s3.NewListObjectsV2Paginator(c.client, &s3.ListObjectsV2Input{
		Bucket:    aws.String(c.bucket),
		Prefix:    aws.String(prefix),
		Delimiter: aws.String("/"), // Only list immediate children
	})

	for paginator.HasMorePages() {
		page, err := paginator.NextPage(ctx)
		if err != nil {
			return nil, fmt.Errorf("failed to list objects: %w", err)
		}

		// Add directories (common prefixes)
		for _, commonPrefix := range page.CommonPrefixes {
			if commonPrefix.Prefix == nil {
				continue
			}

			// Remove the search prefix to get relative path
			relPath := strings.TrimPrefix(*commonPrefix.Prefix, prefix)
			relPath = strings.TrimSuffix(relPath, "/")

			objects = append(objects, S3Object{
				Key:          relPath,
				Size:         0,
				LastModified: time.Now(),
				IsDir:        true,
			})
		}

		// Add files
		for _, obj := range page.Contents {
			if obj.Key == nil {
				continue
			}

			// Skip the prefix itself
			if *obj.Key == prefix {
				continue
			}

			// Remove the search prefix to get relative path
			relPath := strings.TrimPrefix(*obj.Key, prefix)

			// Skip if this is a directory marker
			if strings.HasSuffix(relPath, "/") {
				continue
			}

			objects = append(objects, S3Object{
				Key:          relPath,
				Size:         aws.ToInt64(obj.Size),
				LastModified: aws.ToTime(obj.LastModified),
				IsDir:        false,
			})
		}
	}

	return objects, nil
}

// CreateDirectory creates a directory marker in S3
// S3 doesn't have real directories, but we create empty objects ending with "/"
func (c *S3Client) CreateDirectory(ctx context.Context, path string) error {
	key := c.buildKey(path)
	if !strings.HasSuffix(key, "/") {
		key += "/"
	}

	_, err := c.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
		Body:   bytes.NewReader([]byte{}),
	})
	if err != nil {
		return fmt.Errorf("failed to create directory %s: %w", key, err)
	}

	return nil
}

// DeleteDirectory deletes all objects under a prefix
func (c *S3Client) DeleteDirectory(ctx context.Context, path string) error {
	prefix := c.buildKey(path)
	if !strings.HasSuffix(prefix, "/") {
		prefix += "/"
	}

	// List all objects with this prefix
	var objectsToDelete []types.ObjectIdentifier
	paginator := s3.NewListObjectsV2Paginator(c.client, &s3.ListObjectsV2Input{
		Bucket: aws.String(c.bucket),
		Prefix: aws.String(prefix),
	})

	for paginator.HasMorePages() {
		page, err := paginator.NextPage(ctx)
		if err != nil {
			return fmt.Errorf("failed to list objects for deletion: %w", err)
		}

		for _, obj := range page.Contents {
			objectsToDelete = append(objectsToDelete, types.ObjectIdentifier{
				Key: obj.Key,
			})
		}
	}

	// Delete in batches (S3 allows up to 1000 per request)
	batchSize := 1000
	for i := 0; i < len(objectsToDelete); i += batchSize {
		end := i + batchSize
		if end > len(objectsToDelete) {
			end = len(objectsToDelete)
		}

		_, err := c.client.DeleteObjects(ctx, &s3.DeleteObjectsInput{
			Bucket: aws.String(c.bucket),
			Delete: &types.Delete{
				Objects: objectsToDelete[i:end],
			},
		})
		if err != nil {
			return fmt.Errorf("failed to delete objects: %w", err)
		}
	}

	return nil
}

// ObjectExists checks if an object exists
func (c *S3Client) ObjectExists(ctx context.Context, path string) (bool, error) {
	_, err := c.HeadObject(ctx, path)
	if err != nil {
		// Check if it's a "not found" error
		if strings.Contains(err.Error(), "NotFound") || strings.Contains(err.Error(), "404") {
			return false, nil
		}
		return false, err
	}
	return true, nil
}

// DirectoryExists checks if a directory exists (has objects with the prefix)
// Optimized to use a single ListObjectsV2 call
func (c *S3Client) DirectoryExists(ctx context.Context, path string) (bool, error) {
	// First check if directory marker exists
	dirKey := c.buildKey(path)
	if !strings.HasSuffix(dirKey, "/") {
		dirKey += "/"
	}
	
	// Try HeadObject to check if directory marker exists
	_, err := c.client.HeadObject(ctx, &s3.HeadObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(dirKey),
	})
	if err == nil {
		// Directory marker exists
		return true, nil
	}
	
	// If directory marker doesn't exist, check if there are any objects with this prefix
	prefix := dirKey
	result, err := c.client.ListObjectsV2(ctx, &s3.ListObjectsV2Input{
		Bucket:    aws.String(c.bucket),
		Prefix:    aws.String(prefix),
		MaxKeys:   aws.Int32(1), // Just need to check if any exist
		Delimiter: aws.String("/"),
	})
	if err != nil {
		return false, err
	}

	return len(result.Contents) > 0 || len(result.CommonPrefixes) > 0, nil
}

// CopyObject copies an object within the same bucket
func (c *S3Client) CopyObject(ctx context.Context, srcPath, dstPath string) error {
	srcKey := c.buildKey(srcPath)
	dstKey := c.buildKey(dstPath)

	_, err := c.client.CopyObject(ctx, &s3.CopyObjectInput{
		Bucket:     aws.String(c.bucket),
		CopySource: aws.String(c.bucket + "/" + srcKey),
		Key:        aws.String(dstKey),
	})
	if err != nil {
		return fmt.Errorf("failed to copy object %s -> %s: %w", srcKey, dstKey, err)
	}
	return nil
}

// ListAllObjects lists all objects (recursively) under a given prefix.
// Unlike ListObjects which only lists immediate children, this returns
// every object in the subtree.
func (c *S3Client) ListAllObjects(ctx context.Context, path string) ([]S3Object, error) {
	prefix := c.buildKey(path)
	if prefix != "" && !strings.HasSuffix(prefix, "/") {
		prefix += "/"
	}

	var objects []S3Object
	paginator := s3.NewListObjectsV2Paginator(c.client, &s3.ListObjectsV2Input{
		Bucket: aws.String(c.bucket),
		Prefix: aws.String(prefix),
		// No Delimiter — list all objects recursively
	})

	for paginator.HasMorePages() {
		page, err := paginator.NextPage(ctx)
		if err != nil {
			return nil, fmt.Errorf("failed to list all objects: %w", err)
		}

		for _, obj := range page.Contents {
			if obj.Key == nil {
				continue
			}
			relPath := strings.TrimPrefix(*obj.Key, prefix)
			isDir := strings.HasSuffix(relPath, "/")
			objects = append(objects, S3Object{
				Key:          relPath,
				Size:         aws.ToInt64(obj.Size),
				LastModified: aws.ToTime(obj.LastModified),
				IsDir:        isDir,
			})
		}
	}

	return objects, nil
}

// getParentPath returns the parent directory path
func getParentPath(path string) string {
	if path == "" || path == "/" {
		return ""
	}
	parent := filepath.Dir(path)
	if parent == "." {
		return ""
	}
	return parent
}
