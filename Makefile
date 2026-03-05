# Makefile for OpenViking

# Variables
PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
SETUP_PY := setup.py
AGFS_SERVER_DIR := third_party/agfs/agfs-server
OV_CLI_DIR := crates/ov_cli

# Dependency Versions
MIN_PYTHON_VERSION := 3.10
MIN_GO_VERSION := 1.22
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
	$(AGFS_SERVER_DIR)/build/ \
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
	@echo "  build       - Build AGFS, ov CLI, and C++ extensions using setup.py"
	@echo "  clean       - Remove build artifacts and temporary files"
	@echo "  check-deps  - Check if required dependencies (Go, Rust, CMake, etc.) are installed"
	@echo "  help        - Show this help message"

check-pip:
	@$(PYTHON) -m pip --version > /dev/null 2>&1 || ( \
		echo "Error: pip not found for $(PYTHON)."; \
		echo "Try fixing your virtual environment by running:"; \
		echo "  $(PYTHON) -m ensurepip --upgrade"; \
		exit 1 \
	)

check-deps:
	@echo "Checking dependencies..."
	@# Python check
	@$(PYTHON) -c "import sys; v=sys.version_info; exit(0 if v.major > 3 or (v.major == 3 and v.minor >= 10) else 1)" || (echo "Error: Python >= $(MIN_PYTHON_VERSION) is required."; exit 1)
	@echo "  [OK] Python $$( $(PYTHON) -V | cut -d' ' -f2 )"
	@# Go check
	@command -v go > /dev/null 2>&1 || (echo "Error: Go is not installed."; exit 1)
	@GO_VER=$$(go version | awk '{print $$3}' | sed 's/go//'); \
	$(PYTHON) -c "v='$$GO_VER'.split('.'); exit(0 if int(v[0]) > 1 or (int(v[0]) == 1 and int(v[1]) >= 22) else 1)" || (echo "Error: Go >= $(MIN_GO_VERSION) is required. Found $$GO_VER"; exit 1); \
	echo "  [OK] Go $$GO_VER"
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
	$(PIP) install -e .
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
