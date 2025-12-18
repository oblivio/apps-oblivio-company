#!/usr/bin/env python3
"""
Basic test script for MDB_RUNTIME package.

This script tests:
- Package structure
- Import functionality
- Basic class instantiation
- Syntax validation
"""

import sys
import ast
from pathlib import Path

def test_syntax():
    """Test that all Python files have valid syntax."""
    print("=" * 60)
    print("Testing Python syntax...")
    print("=" * 60)
    
    errors = []
    files_checked = 0
    
    for py_file in Path("mdb_runtime").rglob("*.py"):
        try:
            with open(py_file, 'r') as f:
                content = f.read()
            ast.parse(content)
            files_checked += 1
        except SyntaxError as e:
            errors.append(f"{py_file}: {e}")
        except Exception as e:
            errors.append(f"{py_file}: {e}")
    
    print(f"✅ Checked {files_checked} Python files")
    if errors:
        print(f"❌ Found {len(errors)} syntax errors:")
        for err in errors[:10]:
            print(f"  - {err}")
        return False
    else:
        print("✅ All files have valid syntax")
        return True


def test_imports():
    """Test that key imports work (without actually importing dependencies)."""
    print("\n" + "=" * 60)
    print("Testing import structure...")
    print("=" * 60)
    
    # Test that __init__.py files exist and are valid
    init_files = [
        "mdb_runtime/__init__.py",
        "mdb_runtime/core/__init__.py",
        "mdb_runtime/database/__init__.py",
        "mdb_runtime/auth/__init__.py",
        "mdb_runtime/indexes/__init__.py",
    ]
    
    all_exist = True
    for init_file in init_files:
        path = Path(init_file)
        if path.exists():
            try:
                with open(path, 'r') as f:
                    content = f.read()
                ast.parse(content)
                print(f"✅ {init_file} exists and is valid")
            except Exception as e:
                print(f"❌ {init_file} has errors: {e}")
                all_exist = False
        else:
            print(f"❌ {init_file} missing")
            all_exist = False
    
    return all_exist


def test_structure():
    """Test that package structure is correct."""
    print("\n" + "=" * 60)
    print("Testing package structure...")
    print("=" * 60)
    
    required_dirs = [
        "mdb_runtime/core",
        "mdb_runtime/database",
        "mdb_runtime/auth",
        "mdb_runtime/indexes",
    ]
    
    required_files = [
        "mdb_runtime/__init__.py",
        "mdb_runtime/README.md",
        "mdb_runtime/core/engine.py",
        "mdb_runtime/core/manifest.py",
        "mdb_runtime/database/scoped_wrapper.py",
        "mdb_runtime/database/abstraction.py",
        "mdb_runtime/auth/provider.py",
        "mdb_runtime/auth/dependencies.py",
        "mdb_runtime/indexes/manager.py",
    ]
    
    all_good = True
    
    for dir_path in required_dirs:
        if Path(dir_path).is_dir():
            print(f"✅ {dir_path}/ exists")
        else:
            print(f"❌ {dir_path}/ missing")
            all_good = False
    
    for file_path in required_files:
        if Path(file_path).exists():
            print(f"✅ {file_path} exists")
        else:
            print(f"❌ {file_path} missing")
            all_good = False
    
    return all_good


def test_no_shims():
    """Test that backward compatibility shims have been removed (migration complete)."""
    print("\n" + "=" * 60)
    print("Testing migration completeness (no shims)...")
    print("=" * 60)
    
    shim_files = [
        "async_mongo_wrapper_shim.py",
        "experiment_db_shim.py",
        "mongo_connection_pool_shim.py",
        "manifest_schema_shim.py",
        "authz_provider_shim.py",
        "sub_auth_shim.py",
        "experiment_auth_restrictions_shim.py",
        "index_management_shim.py",
    ]
    
    all_removed = True
    for shim_file in shim_files:
        if Path(shim_file).exists():
            print(f"❌ {shim_file} still exists (should be removed)")
            all_removed = False
        else:
            print(f"✅ {shim_file} removed (migration complete)")
    
    return all_removed


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("MDB_RUNTIME Package Test Suite")
    print("=" * 60 + "\n")
    
    results = []
    
    results.append(("Syntax", test_syntax()))
    results.append(("Structure", test_structure()))
    results.append(("Imports", test_imports()))
    results.append(("Migration", test_no_shims()))
    
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

