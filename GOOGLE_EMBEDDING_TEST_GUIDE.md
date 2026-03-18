# Google/Gemini Embedding 2 Test Guide

This guide provides step-by-step instructions to test the new Google/Gemini Embedding 2 implementation.

## Prerequisites

- Google API Key: `AIzaSyDjMf_tdi8d3Gmbe0LNvJzlA-6ui9dCaio`
- Branch: `feat/google-embedding-native-api`
- PR: https://github.com/volcengine/OpenViking/pull/718

## 1. Environment Setup

```bash
# Navigate to OpenViking directory
cd ~/code/openviking

# Ensure you're on the correct branch
git checkout feat/google-embedding-native-api
git pull fork feat/google-embedding-native-api

# Verify the Google embedder implementation exists
ls -la openviking/models/embedder/google_embedders.py
```

## 2. Build and Install

```bash
# Install OpenViking from source (development mode)
pip install -e .

# Verify installation
ov --version
```

## 3. Configuration

Create test configuration file `~/.openviking/test-google.conf`:

```json
{
  "storage": {
    "workspace": "./test-google-data",
    "vectordb": {
      "name": "test_google_context", 
      "backend": "local"
    },
    "agfs": {
      "port": 1834,
      "log_level": "info",
      "backend": "local"
    }
  },
  "embedding": {
    "dense": {
      "provider": "google",
      "api_key": "AIzaSyDjMf_tdi8d3Gmbe0LNvJzlA-6ui9dCaio",
      "model": "gemini-embedding-2-preview",
      "dimension": 1024,
      "query_param": "RETRIEVAL_QUERY",
      "document_param": "RETRIEVAL_DOCUMENT"
    }
  },
  "vlm": {
    "provider": "openai",
    "api_key": "dummy-key",
    "model": "gpt-4"
  }
}
```

## 4. Basic Functionality Tests

### Test 1: Configuration Validation

```bash
# Test configuration loading
ov --config ~/.openviking/test-google.conf info

# Expected: Should load without errors and show Google provider
```

### Test 2: Add Memory (Single Document)

```bash
# Create test content
echo "The Google Gemini Embedding 2 model supports Matryoshka dimension reduction and task-specific embeddings for improved retrieval performance." > test-content.txt

# Add to memory
ov --config ~/.openviking/test-google.conf add-memory test-content.txt

# Expected: Should embed and store without errors
# Check for API calls in logs
```

### Test 3: Search Basic

```bash
# Search for related content
ov --config ~/.openviking/test-google.conf search "Gemini embedding model"

# Expected: Should find the added content with good relevance score
```

### Test 4: Add Multiple Documents

```bash
# Create multiple test files
echo "Machine learning models require high-quality embeddings for semantic understanding." > ml-content.txt
echo "Vector databases store and retrieve embeddings efficiently for similarity search." > vector-content.txt
echo "Natural language processing uses embeddings to represent text in high-dimensional space." > nlp-content.txt

# Add all to memory
ov --config ~/.openviking/test-google.conf add-memory ml-content.txt vector-content.txt nlp-content.txt

# Expected: Should process all files successfully
```

### Test 5: Search with Different Queries

```bash
# Test various search queries
ov --config ~/.openviking/test-google.conf search "machine learning"
ov --config ~/.openviking/test-google.conf search "vector search"
ov --config ~/.openviking/test-google.conf search "text representation"

# Expected: Should return relevant results with proper ranking
```

## 5. Advanced Feature Tests

### Test 6: Different Dimensions

Create `~/.openviking/test-google-512.conf` with `"dimension": 512`:

```bash
# Test with reduced dimensions (Matryoshka)
ov --config ~/.openviking/test-google-512.conf add-memory test-content.txt
ov --config ~/.openviking/test-google-512.conf search "Gemini"

# Expected: Should work with smaller dimension vectors
```

### Test 7: Task-Specific Parameters

Create `~/.openviking/test-google-enhanced.conf` with enhanced params:

```json
{
  "embedding": {
    "dense": {
      "provider": "google",
      "api_key": "AIzaSyDjMf_tdi8d3Gmbe0LNvJzlA-6ui9dCaio",
      "model": "gemini-embedding-2-preview",
      "dimension": 1024,
      "query_param": "task_type=RETRIEVAL_QUERY,output_dimensionality=1024",
      "document_param": "task_type=RETRIEVAL_DOCUMENT,output_dimensionality=1024"
    }
  }
}
```

```bash
# Test enhanced parameter format
ov --config ~/.openviking/test-google-enhanced.conf add-memory test-content.txt
ov --config ~/.openviking/test-google-enhanced.conf search "Gemini"

# Expected: Should use enhanced parameter format successfully
```

### Test 8: Large Text Chunking

```bash
# Create large text file (>8000 tokens)
python3 -c "
text = 'This is a test of chunking functionality for very long documents. ' * 200
with open('large-content.txt', 'w') as f:
    f.write(text)
"

# Test chunking
ov --config ~/.openviking/test-google.conf add-memory large-content.txt

# Expected: Should handle chunking automatically without errors
```

## 6. Error Handling Tests

### Test 9: Invalid API Key

Create `~/.openviking/test-google-badkey.conf` with invalid API key:

```bash
# Test with bad API key
ov --config ~/.openviking/test-google-badkey.conf add-memory test-content.txt

# Expected: Should fail gracefully with clear error message
```

### Test 10: Invalid Model

Create config with `"model": "invalid-model"`:

```bash
# Test with unsupported model
ov --config ~/.openviking/test-google-badmodel.conf add-memory test-content.txt

# Expected: Should fail with model validation error
```

## 7. Performance Tests

### Test 11: Batch Processing

```bash
# Create multiple files
for i in {1..10}; do
  echo "Test document number $i with unique content about topic $i." > batch-test-$i.txt
done

# Time the batch operation
time ov --config ~/.openviking/test-google.conf add-memory batch-test-*.txt

# Expected: Should process efficiently without rate limiting issues
```

## 8. Verification Checklist

- [ ] Configuration loads without errors
- [ ] Single document embedding works
- [ ] Search returns relevant results  
- [ ] Multiple documents can be added
- [ ] Different search queries work properly
- [ ] Matryoshka dimension reduction (512 dims) works
- [ ] Enhanced parameter format works
- [ ] Large text chunking works automatically
- [ ] Invalid API key fails gracefully
- [ ] Invalid model fails with validation error
- [ ] Batch processing works efficiently
- [ ] No memory leaks or hanging processes
- [ ] API calls use correct Google endpoint format
- [ ] Vector dimensions match configuration

## 9. Debug Commands

If issues arise:

```bash
# Check logs with debug level
ov --config ~/.openviking/test-google.conf --log-level debug add-memory test-content.txt

# Check vector database
ls -la test-google-data/

# Check API calls (if logging enabled)
# Look for requests to generativelanguage.googleapis.com/v1beta
```

## 10. Expected API Format

The implementation should make calls like:

```bash
# Verify this format is used (check logs or network traffic)
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent" \
  -H "Content-Type: application/json" \
  -H "x-goog-api-key: AIzaSyDjMf_tdi8d3Gmbe0LNvJzlA-6ui9dCaio" \
  -d '{ "content": { "parts": [ {"text": "test"} ] } }'
```

## Cleanup

```bash
# Remove test data
rm -rf test-google-data/
rm test-content.txt ml-content.txt vector-content.txt nlp-content.txt large-content.txt batch-test-*.txt
rm ~/.openviking/test-google*.conf
```

## Report Template

**Test Results:**
- [ ] All basic tests passed
- [ ] Advanced features work correctly  
- [ ] Error handling is appropriate
- [ ] Performance is acceptable
- [ ] Issues found: _[list any issues]_
- [ ] Fixes needed: _[list any required fixes]_

**Notes:** _[Add any additional observations]_