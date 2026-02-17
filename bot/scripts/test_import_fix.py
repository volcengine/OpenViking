#!/usr/bin/env python3
"""Test script to verify imports are fixed."""

print("Testing imports...")

try:
    from vikingbot.agent.tools.websearch import WebSearchTool
    print("✓ WebSearchTool imported successfully")
    
    from vikingbot.agent.tools.websearch.registry import registry
    print("✓ Registry imported successfully")
    
    print("\nRegistered backends:", registry.list_names())
    
    print("\n✅ All imports work!")
    
except Exception as e:
    print(f"\n❌ Import failed: {e}")
    import traceback
    traceback.print_exc()
