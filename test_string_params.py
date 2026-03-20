#!/usr/bin/env python3
"""
Test script for OpenAI embedder string parameter parsing
"""

def test_parse_param():
    """Test the parameter parsing logic"""
    
    def parse_param(param):
        """Mock implementation of _parse_param method"""
        if not param:
            return {}
            
        result = {}
        
        # Split by comma for multiple parameters
        parts = [p.strip() for p in param.split(",")]
        
        for part in parts:
            if "=" in part:
                # Explicit key=value format
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()
            else:
                # Default to input_type for backward compatibility
                result["input_type"] = part.strip()
                
        return result
    
    print("Testing parameter parsing logic...")
    
    # Test cases
    test_cases = [
        # (input, expected_output, description)
        (None, {}, "None input"),
        ("", {}, "Empty string"),
        ("query", {"input_type": "query"}, "Simple value (backward compatible)"),
        ("passage", {"input_type": "passage"}, "Simple value (document)"),
        ("input_type=query", {"input_type": "query"}, "Explicit key=value"),
        ("input_type=passage", {"input_type": "passage"}, "Explicit key=value (document)"),
        ("task=search", {"task": "search"}, "Different key"),
        ("input_type=query,task=search", {"input_type": "query", "task": "search"}, "Multiple parameters"),
        ("query,task=search", {"input_type": "query", "task": "search"}, "Mixed format"),
        ("domain=general,input_type=passage,task=retrieval", 
         {"domain": "general", "input_type": "passage", "task": "retrieval"}, "Multiple explicit"),
    ]
    
    all_passed = True
    for i, (input_param, expected, description) in enumerate(test_cases):
        result = parse_param(input_param)
        if result == expected:
            print(f"✓ Test {i+1}: {description}")
            print(f"  Input: {repr(input_param)} -> Output: {result}")
        else:
            print(f"❌ Test {i+1} FAILED: {description}")
            print(f"  Input: {repr(input_param)}")
            print(f"  Expected: {expected}")
            print(f"  Got: {result}")
            all_passed = False
    
    return all_passed

def test_backwards_compatibility():
    """Test that the new format is backward compatible"""
    print("\nTesting backward compatibility...")
    
    # Old style usage (what users might have been doing manually)
    old_patterns = [
        "query",           # Simple task type
        "passage",         # Document type  
        "search_query",    # Specific task
        "search_document"  # Specific document task
    ]
    
    for pattern in old_patterns:
        # This should work seamlessly
        def parse_param(param):
            if not param:
                return {}
            result = {}
            parts = [p.strip() for p in param.split(",")]
            for part in parts:
                if "=" in part:
                    key, value = part.split("=", 1)
                    result[key.strip()] = value.strip()
                else:
                    result["input_type"] = part.strip()
            return result
                    
        result = parse_param(pattern)
        expected = {"input_type": pattern}
        
        if result == expected:
            print(f"✓ Backward compatible: '{pattern}' -> {result}")
        else:
            print(f"❌ Backward compatibility issue: '{pattern}'")
            print(f"  Expected: {expected}, Got: {result}")
            return False
    
    return True

def test_usage_examples():
    """Test real-world usage examples"""
    print("\nTesting usage examples...")
    
    def parse_param(param):
        if not param:
            return {}
        result = {}
        parts = [p.strip() for p in param.split(",")]
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()
            else:
                result["input_type"] = part.strip()
        return result
    
    examples = [
        {
            "name": "Basic OpenAI-compatible server",
            "query_param": "query", 
            "document_param": "passage",
            "expected_query": {"input_type": "query"},
            "expected_doc": {"input_type": "passage"}
        },
        {
            "name": "Advanced OpenAI-compatible server", 
            "query_param": "input_type=query,task=search",
            "document_param": "input_type=passage,task=index",
            "expected_query": {"input_type": "query", "task": "search"},
            "expected_doc": {"input_type": "passage", "task": "index"}
        },
        {
            "name": "Custom server with domain",
            "query_param": "input_type=query,domain=finance,task=search",
            "document_param": "input_type=passage,domain=finance,task=retrieval", 
            "expected_query": {"input_type": "query", "domain": "finance", "task": "search"},
            "expected_doc": {"input_type": "passage", "domain": "finance", "task": "retrieval"}
        }
    ]
    
    for example in examples:
        print(f"\n📝 {example['name']}:")
        
        query_result = parse_param(example["query_param"])
        doc_result = parse_param(example["document_param"])
        
        query_ok = query_result == example["expected_query"]
        doc_ok = doc_result == example["expected_doc"]
        
        if query_ok and doc_ok:
            print(f"  ✓ Query param: '{example['query_param']}' -> {query_result}")
            print(f"  ✓ Document param: '{example['document_param']}' -> {doc_result}")
        else:
            print(f"  ❌ Failed!")
            if not query_ok:
                print(f"    Query: expected {example['expected_query']}, got {query_result}")
            if not doc_ok:
                print(f"    Document: expected {example['expected_doc']}, got {doc_result}")
            return False
    
    return True

if __name__ == "__main__":
    print("OpenAI Embedder String Parameter Test")
    print("=" * 45)
    
    success = True
    success &= test_parse_param()
    success &= test_backwards_compatibility() 
    success &= test_usage_examples()
    
    print("\n" + "=" * 45)
    if success:
        print("✅ All tests passed!")
        print("\n📋 Supported formats:")
        print("  - 'query' -> {'input_type': 'query'}")
        print("  - 'input_type=query' -> {'input_type': 'query'}")
        print("  - 'input_type=query,task=search' -> {'input_type': 'query', 'task': 'search'}")
        print("  - 'query,task=search' -> {'input_type': 'query', 'task': 'search'}")
    else:
        print("❌ Some tests failed")
        exit(1)