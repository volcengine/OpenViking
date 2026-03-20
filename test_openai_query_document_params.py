#!/usr/bin/env python3
"""
Test script for OpenAI embedder query_param and document_param support
"""

import os
import sys
import json

# Add the openviking-test directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from openviking.models.embedder.openai_embedders import (
    OpenAIDenseEmbedder,
    OpenAIQueryEmbedder, 
    OpenAIDocumentEmbedder
)

def test_basic_functionality():
    """Test basic embedder functionality without query/document params"""
    print("Testing basic OpenAI embedder functionality...")
    
    # Mock API key for testing (would fail on actual API call)
    embedder = OpenAIDenseEmbedder(
        model_name="text-embedding-3-small",
        api_key="test-key",
        dimension=1536
    )
    
    print(f"✓ Created embedder with model: {embedder.model_name}")
    print(f"✓ Dimension: {embedder.get_dimension()}")
    print(f"✓ Query params: {embedder.query_param}")
    print(f"✓ Document params: {embedder.document_param}")

def test_query_document_params():
    """Test embedder with query and document parameters"""
    print("\nTesting query and document parameters...")
    
    query_param = {"input_type": "query"}
    document_param = {"input_type": "passage"}
    
    embedder = OpenAIDenseEmbedder(
        model_name="text-embedding-3-small",
        api_key="test-key",
        query_param=query_param,
        document_param=document_param,
        dimension=1536
    )
    
    print(f"✓ Query params: {embedder.query_param}")
    print(f"✓ Document params: {embedder.document_param}")
    
    # Test _build_extra_body method
    query_extra = embedder._build_extra_body(for_query=True)
    doc_extra = embedder._build_extra_body(for_query=False)
    none_extra = embedder._build_extra_body(for_query=None)
    
    print(f"✓ Query extra_body: {query_extra}")
    print(f"✓ Document extra_body: {doc_extra}")
    print(f"✓ Default extra_body: {none_extra}")
    
    assert query_extra == query_param
    assert doc_extra == document_param

def test_specialized_embedders():
    """Test specialized query and document embedders"""
    print("\nTesting specialized embedders...")
    
    query_embedder = OpenAIQueryEmbedder(
        model_name="text-embedding-3-small",
        api_key="test-key",
        query_param={"input_type": "query"},
        dimension=1536
    )
    
    doc_embedder = OpenAIDocumentEmbedder(
        model_name="text-embedding-3-small",
        api_key="test-key", 
        document_param={"input_type": "passage"},
        dimension=1536
    )
    
    print(f"✓ Created OpenAIQueryEmbedder")
    print(f"✓ Created OpenAIDocumentEmbedder")
    print(f"✓ Query embedder params: {query_embedder.query_param}")
    print(f"✓ Doc embedder params: {doc_embedder.document_param}")

def test_backwards_compatibility():
    """Test that existing code without query/document params still works"""
    print("\nTesting backwards compatibility...")
    
    # This should work exactly like before
    embedder = OpenAIDenseEmbedder(
        model_name="text-embedding-3-small",
        api_key="test-key"
    )
    
    print(f"✓ Legacy embedder created successfully")
    print(f"✓ Query params: {embedder.query_param} (empty dict)")
    print(f"✓ Document params: {embedder.document_param} (empty dict)")
    
    # Test that _build_extra_body returns None when no params
    extra = embedder._build_extra_body(for_query=True)
    assert extra == {}, f"Expected empty dict, got {extra}"
    print(f"✓ Extra body returns empty dict when no params")

if __name__ == "__main__":
    print("OpenAI Embedder Query/Document Parameters Test")
    print("=" * 50)
    
    try:
        test_basic_functionality()
        test_query_document_params()
        test_specialized_embedders()
        test_backwards_compatibility()
        
        print("\n" + "=" * 50)
        print("✅ All tests passed!")
        print("\nImplementation supports:")
        print("- query_param and document_param configuration")
        print("- Contextual embedding via for_query parameter")  
        print("- Specialized OpenAIQueryEmbedder and OpenAIDocumentEmbedder classes")
        print("- Full backward compatibility with existing code")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)