# Admin Scripts

Internal administration and maintenance scripts for the project.

## Scripts

### `package_mdb_runtime.sh`

Packages the `mdb_runtime` library for PyPI distribution.

**Usage:**
```bash
./_admin/package_mdb_runtime.sh [patch|minor|major] [--zip-only]
```

**Examples:**
```bash
# Bump patch version and create zip only
./_admin/package_mdb_runtime.sh patch --zip-only

# Bump minor version and build full package (wheel + sdist)
./_admin/package_mdb_runtime.sh minor

# No version bump, just create zip
./_admin/package_mdb_runtime.sh --zip-only
```

**What it does:**
- Reads current version from `mdb_runtime/__init__.py`
- Optionally bumps version (patch/minor/major)
- Updates version in `mdb_runtime/__init__.py`
- Creates `mdb_runtime_pypi/` build directory
- Copies `mdb_runtime` package
- Cleans `__pycache__` files
- Generates `setup.py`, `pyproject.toml`, `MANIFEST.in`, `README.md`
- Optionally builds wheel and sdist (if not `--zip-only`)
- Creates `mdb-runtime-{version}.zip` archive

**Output:**
- `mdb_runtime_pypi/` - Build directory
- `mdb-runtime-{version}.zip` - Distribution zip file
- `mdb_runtime_pypi/dist/` - Wheel and sdist files (if built)

**Note:** This is an internal tool. The generated package should be reviewed and tested before publishing to PyPI.

