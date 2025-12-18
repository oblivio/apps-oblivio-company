#!/usr/bin/env python3
"""
Test script to verify MDB_RUNTIME migration is working correctly.

This script tests:
1. All mdb_runtime imports work
2. Application layer (core_deps) correctly uses mdb_runtime
3. No legacy imports remain
4. Key functionality is accessible
"""

import sys
import ast
from pathlib import Path

def test_mdb_runtime_imports():
    """Test that mdb_runtime can be imported."""
    print("=" * 60)
    print("Testing mdb_runtime imports...")
    print("=" * 60)
    
    errors = []
    
    # Test core imports
    try:
        from mdb_runtime import RuntimeEngine
        print("✅ RuntimeEngine")
    except Exception as e:
        errors.append(f"RuntimeEngine: {e}")
        print(f"❌ RuntimeEngine: {e}")
    
    # Test database imports
    try:
        from mdb_runtime.database import ScopedMongoWrapper, ExperimentDB, get_experiment_db
        print("✅ Database components")
    except Exception as e:
        errors.append(f"Database: {e}")
        print(f"❌ Database: {e}")
    
    # Test auth imports
    try:
        from mdb_runtime.auth import (
            AuthorizationProvider,
            get_current_user,
            require_admin,
            get_experiment_sub_user,
            CasbinAdapter,
            OsoAdapter
        )
        print("✅ Auth components")
    except Exception as e:
        errors.append(f"Auth: {e}")
        print(f"❌ Auth: {e}")
    
    # Test manifest imports
    try:
        from mdb_runtime.core import ManifestValidator, ManifestParser
        print("✅ Manifest components")
    except Exception as e:
        errors.append(f"Manifest: {e}")
        print(f"❌ Manifest: {e}")
    
    # Test index imports
    try:
        from mdb_runtime.indexes import AsyncAtlasIndexManager, run_index_creation_for_collection
        print("✅ Index components")
    except Exception as e:
        errors.append(f"Indexes: {e}")
        print(f"❌ Indexes: {e}")
    
    return len(errors) == 0, errors


def test_application_layer():
    """Test that application layer (core_deps) correctly uses mdb_runtime."""
    print("\n" + "=" * 60)
    print("Testing application layer (core_deps)...")
    print("=" * 60)
    
    errors = []
    
    try:
        # Check that core_deps imports from mdb_runtime
        with open("core_deps.py", "r") as f:
            content = f.read()
        
        # Verify it imports from mdb_runtime
        if "from mdb_runtime" in content:
            print("✅ core_deps imports from mdb_runtime")
        else:
            errors.append("core_deps does not import from mdb_runtime")
            print("❌ core_deps does not import from mdb_runtime")
        
        # Verify no legacy imports
        legacy_imports = [
            "from async_mongo_wrapper",
            "from experiment_db",
            "from authz_provider",
            "from sub_auth",
            "from manifest_schema",
            "from index_management"
        ]
        
        found_legacy = []
        for legacy in legacy_imports:
            if legacy in content:
                found_legacy.append(legacy)
        
        if found_legacy:
            errors.append(f"Legacy imports found: {found_legacy}")
            print(f"❌ Legacy imports found: {found_legacy}")
        else:
            print("✅ No legacy imports in core_deps")
        
        # Test that core_deps functions are accessible
        try:
            from core_deps import (
                get_current_user,
                require_admin,
                get_scoped_db,
                get_experiment_config
            )
            print("✅ core_deps functions accessible")
        except Exception as e:
            errors.append(f"core_deps functions: {e}")
            print(f"❌ core_deps functions: {e}")
            
    except Exception as e:
        errors.append(f"core_deps check: {e}")
        print(f"❌ core_deps check: {e}")
    
    return len(errors) == 0, errors


def test_no_legacy_files():
    """Test that all legacy files have been deleted."""
    print("\n" + "=" * 60)
    print("Testing no legacy files remain...")
    print("=" * 60)
    
    legacy_files = [
        "async_mongo_wrapper.py",
        "experiment_db.py",
        "mongo_connection_pool.py",
        "manifest_schema.py",
        "authz_provider.py",
        "sub_auth.py",
        "experiment_auth_restrictions.py",
        "index_management.py",
    ]
    
    found = []
    for file in legacy_files:
        if Path(file).exists():
            found.append(file)
            print(f"❌ {file} still exists")
        else:
            print(f"✅ {file} deleted")
    
    return len(found) == 0, found


def test_no_legacy_imports():
    """Test that no Python files import from legacy modules."""
    print("\n" + "=" * 60)
    print("Testing no legacy imports in code...")
    print("=" * 60)
    
    legacy_patterns = [
        "from async_mongo_wrapper",
        "import async_mongo_wrapper",
        "from experiment_db",
        "import experiment_db",
        "from mongo_connection_pool",
        "import mongo_connection_pool",
        "from manifest_schema",
        "import manifest_schema",
        "from authz_provider",
        "import authz_provider",
        "from sub_auth",
        "import sub_auth",
        "from experiment_auth_restrictions",
        "import experiment_auth_restrictions",
        "from index_management",
        "import index_management",
    ]
    
    found_imports = []
    
    # Search Python files (excluding mdb_runtime and test files)
    for py_file in Path(".").rglob("*.py"):
        # Skip mdb_runtime, test files, and __pycache__
        if "mdb_runtime" in str(py_file) or "test_" in str(py_file) or "__pycache__" in str(py_file):
            continue
        
        try:
            with open(py_file, "r") as f:
                content = f.read()
            
            for pattern in legacy_patterns:
                if pattern in content:
                    found_imports.append(f"{py_file}: {pattern}")
        except Exception:
            pass
    
    if found_imports:
        print(f"❌ Found {len(found_imports)} legacy imports:")
        for imp in found_imports[:10]:  # Show first 10
            print(f"   - {imp}")
        if len(found_imports) > 10:
            print(f"   ... and {len(found_imports) - 10} more")
    else:
        print("✅ No legacy imports found in code")
    
    return len(found_imports) == 0, found_imports


def test_syntax():
    """Test that all Python files have valid syntax."""
    print("\n" + "=" * 60)
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
    
    # Also check key application files
    app_files = ["core_deps.py", "main.py", "database.py", "role_management.py"]
    for app_file in app_files:
        if Path(app_file).exists():
            try:
                with open(app_file, 'r') as f:
                    content = f.read()
                ast.parse(content)
                files_checked += 1
            except SyntaxError as e:
                errors.append(f"{app_file}: {e}")
            except Exception as e:
                errors.append(f"{app_file}: {e}")
    
    print(f"✅ Checked {files_checked} Python files")
    if errors:
        print(f"❌ Found {len(errors)} syntax errors:")
        for err in errors[:5]:
            print(f"   - {err}")
        return False, errors
    else:
        print("✅ All files have valid syntax")
        return True, []


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("MDB_RUNTIME Migration Test Suite")
    print("=" * 60 + "\n")
    
    results = []
    
    # Run tests
    passed, errors = test_mdb_runtime_imports()
    results.append(("MDB_RUNTIME Imports", passed, errors))
    
    passed, errors = test_application_layer()
    results.append(("Application Layer", passed, errors))
    
    passed, errors = test_no_legacy_files()
    results.append(("No Legacy Files", passed, errors))
    
    passed, errors = test_no_legacy_imports()
    results.append(("No Legacy Imports", passed, errors))
    
    passed, errors = test_syntax()
    results.append(("Syntax Validation", passed, errors))
    
    # Print summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    all_passed = True
    for name, passed, errors in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
        if not passed and errors:
            print(f"   Errors: {len(errors)}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ All tests passed! Migration is complete and working.")
        print("\nNote: core_deps.py is NOT legacy code - it's the application")
        print("      layer that correctly uses mdb_runtime as a library.")
        return 0
    else:
        print("❌ Some tests failed. Review errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

