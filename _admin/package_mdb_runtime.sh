#!/bin/bash
#
# MDB_RUNTIME PyPI Package Builder
# Automatically packages mdb_runtime with version bumping
#
# Usage:
#   ./_admin/package_mdb_runtime.sh [patch|minor|major] [--zip-only]
#
# Examples:
#   ./_admin/package_mdb_runtime.sh patch    # Bump patch version (0.1.0 -> 0.1.1)
#   ./_admin/package_mdb_runtime.sh minor    # Bump minor version (0.1.0 -> 0.2.0)
#   ./_admin/package_mdb_runtime.sh major    # Bump major version (0.1.0 -> 1.0.0)
#   ./_admin/package_mdb_runtime.sh          # No version bump (use current)
#   ./_admin/package_mdb_runtime.sh patch --zip-only  # Only create zip, skip build

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Configuration
PACKAGE_NAME="mdb-runtime"
PACKAGE_DIR="mdb_runtime"
BUILD_DIR="mdb_runtime_pypi"
VERSION_FILE="${PACKAGE_DIR}/__init__.py"
ZIP_ONLY=false

# Parse arguments
BUMP_TYPE=""
ZIP_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --zip-only)
            ZIP_ONLY=true
            ;;
        --help|-h)
            echo "Usage: $0 [patch|minor|major] [--zip-only]"
            echo ""
            echo "Examples:"
            echo "  $0 patch         # Bump patch version (0.1.0 -> 0.1.1)"
            echo "  $0 minor         # Bump minor version (0.1.0 -> 0.2.0)"
            echo "  $0 major         # Bump major version (0.1.0 -> 1.0.0)"
            echo "  $0               # No version bump (use current)"
            echo "  $0 patch --zip-only  # Only create zip, skip build"
            exit 0
            ;;
        patch|minor|major)
            BUMP_TYPE="$arg"
            ;;
        *)
            if [[ -z "$BUMP_TYPE" ]] && [[ "$arg" != "--zip-only" ]]; then
                echo -e "${RED}Error: Unknown argument '$arg'${NC}" >&2
                echo "Use --help for usage information" >&2
                exit 1
            fi
            ;;
    esac
done

# Function to extract current version
get_current_version() {
    if [[ -f "$VERSION_FILE" ]]; then
        # Use sed for cross-platform compatibility
        sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$VERSION_FILE" | head -1 || echo "0.1.0"
    else
        echo "0.1.0"
    fi
}

# Function to bump version
bump_version() {
    local version=$1
    local bump_type=$2
    
    IFS='.' read -ra VERSION_PARTS <<< "$version"
    local major=${VERSION_PARTS[0]:-0}
    local minor=${VERSION_PARTS[1]:-0}
    local patch=${VERSION_PARTS[2]:-0}
    
    case "$bump_type" in
        major)
            major=$((major + 1))
            minor=0
            patch=0
            ;;
        minor)
            minor=$((minor + 1))
            patch=0
            ;;
        patch)
            patch=$((patch + 1))
            ;;
        *)
            echo -e "${RED}Error: Invalid bump type '$bump_type'. Use patch, minor, or major${NC}" >&2
            exit 1
            ;;
    esac
    
    echo "${major}.${minor}.${patch}"
}

# Function to update version in __init__.py
update_package_version() {
    local new_version=$1
    local file="$VERSION_FILE"
    
    if [[ -f "$file" ]]; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            # macOS
            sed -i '' "s/__version__ = \".*\"/__version__ = \"$new_version\"/" "$file"
        else
            # Linux
            sed -i "s/__version__ = \".*\"/__version__ = \"$new_version\"/" "$file"
        fi
        echo -e "${GREEN}✓${NC} Updated version in $file to $new_version"
    fi
}

# Function to create setup.py
create_setup_py() {
    local version=$1
    local readme_file="${PACKAGE_DIR}/README.md"
    local long_desc=""
    
    if [[ -f "$readme_file" ]]; then
        long_desc=$(cat "$readme_file")
    fi
    
    cat > "${BUILD_DIR}/setup.py" << EOF
"""
Setup configuration for MDB_RUNTIME package.
"""
from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
readme_file = Path(__file__).parent / "${PACKAGE_DIR}" / "README.md"
long_description = ""
if readme_file.exists():
    long_description = readme_file.read_text(encoding="utf-8")

setup(
    name="${PACKAGE_NAME}",
    version="${version}",
    description="MongoDB Multi-Tenant Experiment Runtime Engine",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Your Name",
    author_email="your.email@example.com",
    url="https://github.com/yourusername/${PACKAGE_NAME}",
    packages=find_packages(exclude=["tests", "tests.*"]),
    python_requires=">=3.8",
    install_requires=[
        "motor>=3.0.0",
        "pymongo>=4.0.0",
        "fastapi>=0.100.0",
        "pydantic>=2.0.0",
        "pyjwt>=2.8.0",
    ],
    extras_require={
        "ray": ["ray>=2.0.0"],
        "casbin": ["casbin>=1.0.0", "casbin-motor-adapter>=0.1.0"],
        "oso": ["oso>=0.27.0"],
        "all": [
            "ray>=2.0.0",
            "casbin>=1.0.0",
            "casbin-motor-adapter>=0.1.0",
            "oso>=0.27.0",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Database",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords="mongodb multi-tenant runtime engine database scoping",
    include_package_data=True,
)
EOF
}

# Function to create pyproject.toml
create_pyproject_toml() {
    local version=$1
    
    cat > "${BUILD_DIR}/pyproject.toml" << EOF
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "${PACKAGE_NAME}"
version = "${version}"
description = "MongoDB Multi-Tenant Experiment Runtime Engine"
readme = "${PACKAGE_DIR}/README.md"
requires-python = ">=3.8"
license = {text = "MIT"}
authors = [
    {name = "Your Name", email = "your.email@example.com"}
]
keywords = ["mongodb", "multi-tenant", "runtime", "engine", "database", "scoping"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.8",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Topic :: Database",
    "Topic :: Software Development :: Libraries :: Python Modules",
]

dependencies = [
    "motor>=3.0.0",
    "pymongo>=4.0.0",
    "fastapi>=0.100.0",
    "pydantic>=2.0.0",
    "pyjwt>=2.8.0",
]

[project.optional-dependencies]
ray = ["ray>=2.0.0"]
casbin = ["casbin>=1.0.0", "casbin-motor-adapter>=0.1.0"]
oso = ["oso>=0.27.0"]
all = [
    "ray>=2.0.0",
    "casbin>=1.0.0",
    "casbin-motor-adapter>=0.1.0",
    "oso>=0.27.0",
]

[project.urls]
Homepage = "https://github.com/yourusername/${PACKAGE_NAME}"
Documentation = "https://github.com/yourusername/${PACKAGE_NAME}#readme"
Repository = "https://github.com/yourusername/${PACKAGE_NAME}"
Issues = "https://github.com/yourusername/${PACKAGE_NAME}/issues"
EOF
}

# Function to create MANIFEST.in
create_manifest() {
    cat > "${BUILD_DIR}/MANIFEST.in" << EOF
include LICENSE
include README.md
include pyproject.toml
recursive-include ${PACKAGE_DIR} *.py
recursive-include ${PACKAGE_DIR} *.md
EOF
}

# Function to create top-level README
create_readme() {
    cat > "${BUILD_DIR}/README.md" << EOF
# ${PACKAGE_NAME}

MongoDB Multi-Tenant Experiment Runtime Engine

## Installation

\`\`\`bash
pip install ${PACKAGE_NAME}
\`\`\`

## Quick Start

\`\`\`python
from mdb_runtime import RuntimeEngine

# Initialize engine
engine = RuntimeEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)
await engine.initialize()

# Get scoped database
db = engine.get_scoped_db("my_experiment")
doc = await db.my_collection.find_one({"name": "test"})
\`\`\`

## Features

- **Multi-tenant database scoping** - Automatic experiment isolation
- **Authentication & Authorization** - Built-in auth with Casbin/OSO support
- **Manifest validation** - JSON schema validation with versioning
- **Index management** - Automatic Atlas Search and Vector index management
- **Runtime engine** - Centralized orchestration for all components

## Documentation

See \`${PACKAGE_DIR}/README.md\` for detailed documentation.

## License

MIT License
EOF
}

# Main execution
main() {
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     MDB_RUNTIME PyPI Package Builder                    ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    # Get current version
    CURRENT_VERSION=$(get_current_version)
    echo -e "${YELLOW}Current version: ${CURRENT_VERSION}${NC}"
    
    # Determine new version
    if [[ -n "$BUMP_TYPE" ]]; then
        NEW_VERSION=$(bump_version "$CURRENT_VERSION" "$BUMP_TYPE")
        echo -e "${YELLOW}Bumping ${BUMP_TYPE} version: ${CURRENT_VERSION} → ${NEW_VERSION}${NC}"
        update_package_version "$NEW_VERSION"
    else
        NEW_VERSION="$CURRENT_VERSION"
        echo -e "${YELLOW}Using current version: ${NEW_VERSION}${NC}"
    fi
    
    # Clean build directory
    echo -e "\n${BLUE}Cleaning build directory...${NC}"
    rm -rf "$BUILD_DIR"
    mkdir -p "$BUILD_DIR"
    
    # Copy package
    echo -e "${BLUE}Copying ${PACKAGE_DIR} package...${NC}"
    cp -r "$PACKAGE_DIR" "$BUILD_DIR/"
    
    # Clean __pycache__
    echo -e "${BLUE}Cleaning __pycache__...${NC}"
    find "$BUILD_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
    find "$BUILD_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    
    # Create package files
    echo -e "${BLUE}Creating package files...${NC}"
    create_setup_py "$NEW_VERSION"
    create_pyproject_toml "$NEW_VERSION"
    create_manifest
    create_readme
    
    # Copy LICENSE if exists
    if [[ -f "LICENSE" ]]; then
        cp LICENSE "$BUILD_DIR/"
        echo -e "${GREEN}✓${NC} Copied LICENSE"
    else
        echo "# MIT License placeholder - Update with your license" > "${BUILD_DIR}/LICENSE"
        echo -e "${YELLOW}⚠${NC} Created placeholder LICENSE (update with your license)"
    fi
    
    # Build package if not zip-only
    if [[ "$ZIP_ONLY" == false ]]; then
        echo -e "\n${BLUE}Building package (wheel + sdist)...${NC}"
        cd "$BUILD_DIR"
        
        # Check if build is installed
        if ! python -m build --version &>/dev/null; then
            echo -e "${YELLOW}Installing build tools...${NC}"
            pip install build wheel -q
        fi
        
        python -m build
        cd ..
        
        echo -e "${GREEN}✓${NC} Package built successfully"
        echo -e "${GREEN}  dist/${PACKAGE_NAME}-${NEW_VERSION}-py3-none-any.whl${NC}"
        echo -e "${GREEN}  dist/${PACKAGE_NAME}-${NEW_VERSION}.tar.gz${NC}"
    fi
    
    # Create zip file
    echo -e "\n${BLUE}Creating zip archive...${NC}"
    cd "$BUILD_DIR"
    ZIP_FILE="${PROJECT_ROOT}/${PACKAGE_NAME}-${NEW_VERSION}.zip"
    zip -r "$ZIP_FILE" . -x "*.pyc" -x "*__pycache__*" -x "*.DS_Store" -x "dist/*" -x "build/*" -x "*.egg-info/*" > /dev/null
    cd ..
    
    if [[ -f "$ZIP_FILE" ]]; then
        ZIP_SIZE=$(du -h "$ZIP_FILE" | cut -f1)
        echo -e "${GREEN}✓${NC} Created ${ZIP_FILE} (${ZIP_SIZE})"
    else
        echo -e "${RED}✗${NC} Failed to create zip file"
        exit 1
    fi
    
    # Summary
    echo -e "\n${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                    Package Ready!                        ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${BLUE}Version:${NC} ${NEW_VERSION}"
    echo -e "${BLUE}Package:${NC} ${PACKAGE_NAME}"
    echo -e "${BLUE}Zip file:${NC} ${ZIP_FILE}"
    
    if [[ "$ZIP_ONLY" == false ]]; then
        echo -e "${BLUE}Build files:${NC} ${BUILD_DIR}/dist/"
        echo ""
        echo -e "${YELLOW}To publish to PyPI:${NC}"
        echo -e "  python -m twine upload ${BUILD_DIR}/dist/*"
        echo ""
        echo -e "${YELLOW}To test installation:${NC}"
        echo -e "  pip install ${BUILD_DIR}/dist/${PACKAGE_NAME}-${NEW_VERSION}-py3-none-any.whl"
    fi
    
    echo ""
}

# Run main
main

