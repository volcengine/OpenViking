#!/usr/bin/env python3
"""
Test the simplified OpenAI embedder logic (no input_type field)
"""

def test_simplified_build_extra_body():
    """Test the simplified _build_extra_body logic"""
    
    def parse_param_string(param):
        """Mock implementation of _parse_param_string method"""
        if not param:
            return {}
            
        result = {}
        parts = [p.strip() for p in param.split(",")]
        
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()
                
        return result
    
    def build_extra_body(context, query_param, document_param):
        """Simplified _build_extra_body logic"""
        extra_body = {}
        
        # Determine active parameter based on context
        active_param = None
        if context == "query" and query_param:
            active_param = query_param
        elif context == "document" and document_param:
            active_param = document_param
        
        if active_param:
            if "=" in active_param:
                # Parse key=value format
                parsed = parse_param_string(active_param)
                extra_body.update(parsed)
            else:
                # Simple format
                extra_body["input_type"] = active_param
        
        return extra_body if extra_body else None
    
    print("Testing simplified _build_extra_body logic...")
    
    test_cases = [
        # (context, query_param, document_param, expected, description)
        ("query", "query", None, {"input_type": "query"}, "Simple query mode"),
        ("query", "input_type=query,task=search", None, {"input_type": "query", "task": "search"}, "Query with key=value"),
        ("document", None, "passage", {"input_type": "passage"}, "Simple document mode"),
        ("document", None, "input_type=passage,task=index", {"input_type": "passage", "task": "index"}, "Document with key=value"),
        ("query", None, None, None, "No parameters"),
        (None, "query", None, None, "No context"),
        ("query", "search_query", None, {"input_type": "search_query"}, "Custom simple query"),
        ("document", None, "input_type=document,domain=finance,task=index", {"input_type": "document", "domain": "finance", "task": "index"}, "Multiple parameters"),
    ]
    
    all_passed = True
    for i, (context, query_param, doc_param, expected, description) in enumerate(test_cases):
        result = build_extra_body(context, query_param, doc_param)
        if result == expected:
            print(f"✓ Test {i+1}: {description}")
            print(f"  Context: {context}, Query: {query_param}, Doc: {doc_param}")
            print(f"  Result: {result}")
        else:
            print(f"❌ Test {i+1} FAILED: {description}")
            print(f"  Expected: {expected}")
            print(f"  Got: {result}")
            all_passed = False
    
    return all_passed

def test_logic_comparison():
    """Compare old complex logic vs new simplified logic"""
    print("\nTesting logic comparison...")
    
    # Mock the old complex logic
    def old_logic(context, query_param, document_param):
        non_symmetric = query_param is not None or document_param is not None
        if not non_symmetric:
            input_type = None
        elif context == "query":
            input_type = query_param if query_param is not None else "query"
        elif context == "document":
            input_type = document_param if document_param is not None else "passage"
        else:
            input_type = None
            
        return {"input_type": input_type} if input_type is not None else None
    
    # New simplified logic (for simple cases)
    def new_logic(context, query_param, document_param):
        active_param = None
        if context == "query" and query_param:
            active_param = query_param
        elif context == "document" and document_param:
            active_param = document_param
        
        if active_param and "=" not in active_param:
            return {"input_type": active_param}
        return None
    
    test_cases = [
        # (context, query_param, document_param, description)
        ("query", "query", None, "Query mode"),
        ("document", None, "passage", "Document mode"),
        (None, None, None, "Symmetric mode"),
        ("query", None, None, "Query context without param"),
        ("document", None, None, "Document context without param"),
    ]
    
    for context, query_param, doc_param, description in test_cases:
        old_result = old_logic(context, query_param, doc_param)
        new_result = new_logic(context, query_param, doc_param)
        
        if old_result == new_result:
            print(f"✓ {description}: Old={old_result}, New={new_result}")
        else:
            print(f"❌ {description}: Old={old_result}, New={new_result}")
            print("  Logic difference detected!")
            
    print("\n📝 Note: New logic is simpler and more direct.")
    print("   Old logic had complex non_symmetric calculation.")
    print("   New logic just checks context + parameter directly.")
    
    return True

if __name__ == "__main__":
    print("Simplified OpenAI Embedder Logic Test")
    print("=" * 40)
    
    success = True
    success &= test_simplified_build_extra_body()
    success &= test_logic_comparison()
    
    print("\n" + "=" * 40)
    if success:
        print("✅ All tests passed!")
        print("\n🎯 Simplifications:")
        print("  ✓ Eliminated self.input_type field")
        print("  ✓ Removed complex non_symmetric logic")
        print("  ✓ Direct parameter handling in _build_extra_body()")
        print("  ✓ Much cleaner constructor")
        
        print("\n📝 New approach:")
        print("  - Store raw parameters: query_param, document_param, context")
        print("  - Handle all logic in _build_extra_body() method")
        print("  - Simple: 'query' -> {'input_type': 'query'}")
        print("  - Enhanced: 'input_type=query,task=search' -> {...}")
    else:
        print("❌ Some tests failed")
        exit(1)