# Makefile for OpenViking

# Variables
PYTHON ?= python3
SETUP_PY := setup.py
OV_CLI_DIR := crates/ov_cli

# Dependency Versions
MIN_PYTHON_VERSION := 3.10
MIN_CMAKE_VERSION := 3.12
MIN_RUST_VERSION := 1.88
MIN_GCC_VERSION := 9
MIN_CLANG_VERSION := 11

# Output directories to clean
CLEAN_DIRS := \
	build/ \
	dist/ \
	*.egg-info/ \
	openviking/bin/ \
	openviking/lib/ \
	$(OV_CLI_DIR)/target/ \
	src/cmake_build/ \
	.pytest_cache/ \
	.coverage \
	htmlcov/ \
	**/__pycache__/

.PHONY: all build clean help check-pip check-deps

all: build

help:
	@echo "Available targets:"
	@echo "  build       - Build ragfs-python and C++ extensions using setup.py"
	@echo "  clean       - Remove build artifacts and temporary files"
	@echo "  check-deps  - Check if required dependencies (Rust, CMake, etc.) are installed"
	@echo "  help        - Show this help message"

check-pip:
	@if command -v uv > /dev/null 2>&1 && uv pip --help > /dev/null 2>&1; then \
		echo "  [OK] uv pip found"; \
	elif $(PYTHON) -m pip --version > /dev/null 2>&1; then \
		echo "  [OK] pip found"; \
	else \
		echo "Error: Neither uv pip nor pip found for $(PYTHON)."; \
		echo "Try fixing your environment by running:"; \
		echo "  uv sync          # if using uv"; \
		echo "  or"; \
		echo "  $(PYTHON) -m ensurepip --upgrade"; \
		exit 1; \
	fi

check-deps:
	@echo "Checking dependencies..."
	@# Python check
	@$(PYTHON) -c "import sys; v=sys.version_info; exit(0 if v.major > 3 or (v.major == 3 and v.minor >= 10) else 1)" || (echo "Error: Python >= $(MIN_PYTHON_VERSION) is required."; exit 1)
	@echo "  [OK] Python $$( $(PYTHON) -V | cut -d' ' -f2 )"
	@# CMake check
	@command -v cmake > /dev/null 2>&1 || (echo "Error: CMake is not installed."; exit 1)
	@CMAKE_VER=$$(cmake --version | head -n1 | awk '{print $$3}'); \
	$(PYTHON) -c "v='$$CMAKE_VER'.split('.'); exit(0 if int(v[0]) > 3 or (int(v[0]) == 3 and int(v[1]) >= 12) else 1)" || (echo "Error: CMake >= $(MIN_CMAKE_VERSION) is required. Found $$CMAKE_VER"; exit 1); \
	echo "  [OK] CMake $$CMAKE_VER"
	@# Rust check
	@command -v rustc > /dev/null 2>&1 || (echo "Error: Rust is not installed."; exit 1)
	@RUST_VER=$$(rustc --version | awk '{print $$2}'); \
	$(PYTHON) -c "v='$$RUST_VER'.split('.'); exit(0 if int(v[0]) > 1 or (int(v[0]) == 1 and int(v[1]) >= 88) else 1)" || (echo "Error: Rust >= $(MIN_RUST_VERSION) is required. Found $$RUST_VER"; exit 1); \
	echo "  [OK] Rust $$RUST_VER"
	@# C++ Compiler check
	@if command -v clang++ > /dev/null 2>&1; then \
		CLANG_VER_FULL=$$(clang++ --version | head -n1 | grep -oE "[0-9]+\.[0-9]+\.[0-9]+" | head -n1); \
		CLANG_VER=$$(echo $$CLANG_VER_FULL | cut -d. -f1); \
		if [ $$CLANG_VER -lt $(MIN_CLANG_VERSION) ]; then echo "Error: Clang >= $(MIN_CLANG_VERSION) is required. Found $$CLANG_VER_FULL"; exit 1; fi; \
		echo "  [OK] Clang $$CLANG_VER_FULL"; \
	elif command -v g++ > /dev/null 2>&1; then \
		GCC_VER_FULL=$$(g++ -dumpversion); \
		GCC_VER=$$(echo $$GCC_VER_FULL | cut -d. -f1); \
		if [ $$GCC_VER -lt $(MIN_GCC_VERSION) ]; then echo "Error: GCC >= $(MIN_GCC_VERSION) is required. Found $$GCC_VER_FULL"; exit 1; fi; \
		echo "  [OK] GCC $$GCC_VER_FULL"; \
	else \
		echo "Error: C++ compiler (GCC or Clang) is required."; exit 1; \
	fi

build: check-deps check-pip
	@echo "Starting build process via setup.py..."
	$(PYTHON) $(SETUP_PY) build_ext --inplace
	@if command -v uv > /dev/null 2>&1 && uv pip --help > /dev/null 2>&1; then \
		echo "  [OK] uv pip found, use uv pip to install..."; \
		uv pip install -e .; \
	else \
		echo "  [OK] pip found, use pip to install..."; \
		$(PYTHON) -m pip install -e .; \
	fi
	@echo "Building ragfs-python (Rust RAGFS binding) into openviking/lib/..."
	@MATURIN_CMD=""; \
	if command -v maturin > /dev/null 2>&1; then \
		MATURIN_CMD=maturin; \
	elif command -v uv > /dev/null 2>&1 && uv pip --help > /dev/null 2>&1; then \
		uv pip install maturin && MATURIN_CMD=maturin; \
	fi; \
	if [ -n "$$MATURIN_CMD" ]; then \
		TMPDIR=$$(mktemp -d); \
		cd crates/ragfs-python && $$MATURIN_CMD build --release --features s3 --out "$$TMPDIR" 2>&1; \
		cd ../..; \
		mkdir -p openviking/lib; \
		echo "import zipfile, glob, shutil, os, sys" > /tmp/extract_ragfs.py; \
		echo "whls = glob.glob(os.path.join('$$TMPDIR', 'ragfs_python-*.whl'))" >> /tmp/extract_ragfs.py; \
		echo "assert whls, 'maturin produced no wheel'" >> /tmp/extract_ragfs.py; \
		echo "with zipfile.ZipFile(whls[0]) as zf:" >> /tmp/extract_ragfs.py; \
		echo "    for name in zf.namelist():" >> /tmp/extract_ragfs.py; \
		echo "        bn = os.path.basename(name)" >> /tmp/extract_ragfs.py; \
		echo "        if bn.startswith('ragfs_python') and (bn.endswith('.so') or bn.endswith('.pyd')):" >> /tmp/extract_ragfs.py; \
		echo "            dst = os.path.join('openviking', 'lib', bn)" >> /tmp/extract_ragfs.py; \
		echo "            with zf.open(name) as src, open(dst, 'wb') as f: f.write(src.read())" >> /tmp/extract_ragfs.py; \
		echo "            os.chmod(dst, 0o755)" >> /tmp/extract_ragfs.py; \
		echo "            print(f'  [OK] ragfs-python: extracted {bn} -> {dst}')" >> /tmp/extract_ragfs.py; \
		echo "            sys.exit(0)" >> /tmp/extract_ragfs.py; \
		echo "print('[Warning] No ragfs_python .so/.pyd found in wheel')" >> /tmp/extract_ragfs.py; \
		echo "sys.exit(1)" >> /tmp/extract_ragfs.py; \
		$(PYTHON) /tmp/extract_ragfs.py; \
		rm -f /tmp/extract_ragfs.py; \
		rm -rf "$$TMPDIR"; \
	else \
		echo "  [SKIP] maturin not found, ragfs-python (Rust binding) will not be built."; \
		echo "         Install maturin to enable: uv pip install maturin"; \
	fi
	@echo "Build completed successfully."

clean:
	@echo "Cleaning up build artifacts..."
	@for dir in $(CLEAN_DIRS); do \
		if [ -d "$$dir" ] || [ -f "$$dir" ]; then \
			echo "Removing $$dir"; \
			rm -rf $$dir; \
		fi \
	done
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} +
	@echo "Cleanup completed."
