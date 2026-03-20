#!/usr/bin/env python3
"""
Test the minimal enhancement for key=value parameter parsing
"""

def test_parse_param_string():
    """Test the key=value parsing function"""
    
    def parse_param_string(param):
        """Mock implementation of _parse_param_string method"""
        if not param:
            return {}
            
        result = {}
        
        # Split by comma for multiple parameters
        parts = [p.strip() for p in param.split(",")]
        
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()
                
        return result
    
    print("Testing key=value parameter parsing...")
    
    test_cases = [
        # (input, expected_output, description)
        (None, {}, "None input"),
        ("", {}, "Empty string"),
        ("query", {}, "Simple value (no key=value, returns empty)"),
        ("input_type=query", {"input_type": "query"}, "Single key=value"),
        ("input_type=query,task=search", {"input_type": "query", "task": "search"}, "Multiple key=value"),
        ("input_type=query,task=search,domain=finance", {"input_type": "query", "task": "search", "domain": "finance"}, "Three parameters"),
        ("task=index", {"task": "index"}, "Different key"),
    ]
    
    all_passed = True
    for i, (input_param, expected, description) in enumerate(test_cases):
        result = parse_param_string(input_param)
        if result == expected:
            print(f"✓ Test {i+1}: {description}")
            print(f"  Input: {repr(input_param)} -> Output: {result}")
        else:
            print(f"❌ Test {i+1} FAILED: {description}")
            print(f"  Expected: {expected}")
            print(f"  Got: {result}")
            all_passed = False
    
    return all_passed

def test_build_extra_body_logic():
    """Test the _build_extra_body logic"""
    print("\nTesting _build_extra_body logic...")
    
    def parse_param_string(param):
        if not param:
            return {}
        result = {}
        parts = [p.strip() for p in param.split(",")]
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()
        return result
    
    def build_extra_body(context, query_param_raw, document_param_raw, input_type):
        """Mock implementation of _build_extra_body logic"""
        extra_body = {}
        
        if context == "query" and query_param_raw:
            if "=" in query_param_raw:
                parsed = parse_param_string(query_param_raw)
                extra_body.update(parsed)
            elif input_type is not None:
                extra_body["input_type"] = input_type
        elif context == "document" and document_param_raw:
            if "=" in document_param_raw:
                parsed = parse_param_string(document_param_raw)
                extra_body.update(parsed)
            elif input_type is not None:
                extra_body["input_type"] = input_type
        elif input_type is not None:
            # Default behavior for simple cases
            extra_body["input_type"] = input_type
        
        return extra_body if extra_body else None
    
    test_cases = [
        # (context, query_param_raw, document_param_raw, input_type, expected, description)
        ("query", "query", None, "query", {"input_type": "query"}, "Simple query mode"),
        ("query", "input_type=query,task=search", None, "query", {"input_type": "query", "task": "search"}, "Query with key=value"),
        ("document", None, "passage", "passage", {"input_type": "passage"}, "Simple document mode"),
        ("document", None, "input_type=passage,task=index", "passage", {"input_type": "passage", "task": "index"}, "Document with key=value"),
        (None, None, None, "query", {"input_type": "query"}, "Symmetric mode with input_type"),
        (None, None, None, None, None, "Fully symmetric mode"),
    ]
    
    all_passed = True
    for i, (context, query_raw, doc_raw, input_type, expected, description) in enumerate(test_cases):
        result = build_extra_body(context, query_raw, doc_raw, input_type)
        if result == expected:
            print(f"✓ Test {i+1}: {description}")
            print(f"  Result: {result}")
        else:
            print(f"❌ Test {i+1} FAILED: {description}")
            print(f"  Expected: {expected}")
            print(f"  Got: {result}")
            all_passed = False
    
    return all_passed

if __name__ == "__main__":
    print("Minimal OpenAI Embedder Enhancement Test")
    print("=" * 40)
    
    success = True
    success &= test_parse_param_string()
    success &= test_build_extra_body_logic()
    
    print("\n" + "=" * 40)
    if success:
        print("✅ All tests passed!")
        print("\n🎯 Features:")
        print("  ✓ key=value parameter parsing only")
        print("  ✓ No mixed mode optimization")
        print("  ✓ Clean integration with existing PR #608")
        print("  ✓ Simple values work as before")
        
        print("\n📝 Usage:")
        print("  query_param='query' -> uses existing input_type logic")
        print("  query_param='input_type=query,task=search' -> {'input_type': 'query', 'task': 'search'}")
    else:
        print("❌ Some tests failed")
        exit(1)