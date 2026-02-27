.PHONY: all build run test clean install help lint deps

# Variables
BINARY_NAME=agfs-server
BUILD_DIR=build
CMD_DIR=cmd/server
GO=go
GOFLAGS=-v
ADDR?=:8080

# Build information
VERSION?=$(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
BUILD_TIME=$(shell date -u '+%Y-%m-%d_%H:%M:%S')
GIT_COMMIT=$(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")

# LDFLAGS for build information
LDFLAGS=-ldflags "-X main.Version=$(VERSION) -X main.BuildTime=$(BUILD_TIME) -X main.GitCommit=$(GIT_COMMIT)"

all: build ## Build the project (default target)

build: ## Build the server binary
	@echo "Building $(BINARY_NAME)..."
	@mkdir -p $(BUILD_DIR)
	$(GO) build $(GOFLAGS) $(LDFLAGS) -o $(BUILD_DIR)/$(BINARY_NAME) $(CMD_DIR)/main.go
	@echo "Build complete: $(BUILD_DIR)/$(BINARY_NAME)"

build-lib: ## Build AGFS binding library
	@echo "Detecting OS for building binding library..."
	@OS=$$(uname -s | tr '[:upper:]' '[:lower:]'); \
	LIB_NAME=libagfsbinding; \
	CMD_PYBINDING_DIR=cmd/pybinding; \
	case "$$OS" in \
		darwin*) \
			echo "Building for macOS..."; \
			CGO_ENABLED=1 $(GO) build -buildmode=c-shared -o $(BUILD_DIR)/$$LIB_NAME.dylib $$CMD_PYBINDING_DIR/main.go; \
			;; \
		linux*) \
			echo "Building for Linux..."; \
			CGO_ENABLED=1 $(GO) build -buildmode=c-shared -o $(BUILD_DIR)/$$LIB_NAME.so $$CMD_PYBINDING_DIR/main.go; \
			;; \
		msys*|cygwin*|mingw*) \
			echo "Building for Windows..."; \
			CC=x86_64-w64-mingw32-gcc CXX=x86_64-w64-mingw32-g++ AR=x86_64-w64-mingw32-ar GOOS=windows GOARCH=amd64 CGO_ENABLED=1 $(GO) build -buildmode=c-shared -o $(BUILD_DIR)/$$LIB_NAME.dll $$CMD_PYBINDING_DIR/main.go; \
			;; \
		*) echo "Unsupported OS: $$OS"; exit 1 ;; \
	esac; \
	echo "Build complete in $(BUILD_DIR)"

run: build
	@echo "Starting $(BINARY_NAME) on $(ADDR)..."
	./$(BUILD_DIR)/$(BINARY_NAME) -addr $(ADDR)

dev: ## Run the server in development mode (without building binary)
	@echo "Running server in development mode on $(ADDR)..."
	$(GO) run $(CMD_DIR)/main.go -addr $(ADDR)

install: build ## Install the binary to $GOPATH/bin
	@echo "Installing $(BINARY_NAME) to $(GOPATH)/bin..."
	$(GO) install $(LDFLAGS) $(CMD_DIR)/main.go
	@echo "Installed successfully"

test: ## Run all tests
	@echo "Running tests..."
	$(GO) test -v ./...

lint: ## Run golangci-lint (requires golangci-lint to be installed)
	@echo "Running golangci-lint..."
	@if command -v golangci-lint >/dev/null 2>&1; then \
		golangci-lint run ./...; \
	else \
		echo "golangci-lint not installed. Install it from https://golangci-lint.run/usage/install/"; \
		exit 1; \
	fi

deps: ## Download dependencies
	$(GO) mod download
	$(GO) mod tidy

deps-update: ## Update dependencies
	$(GO) get -u ./...
	$(GO) mod tidy

clean: ## Clean build artifacts
	@echo "Cleaning..."
	@rm -rf $(BUILD_DIR)
	@rm -f coverage.out coverage.html
	@rm -f $(BINARY_NAME)
	@echo "Clean complete"

docker-build: ## Build Docker image
	@echo "Building Docker image..."
	docker build -t agfs-server:$(VERSION) .

docker-run: ## Run Docker container
	@echo "Running Docker container..."
	docker run -p 8080:8080 agfs-server:$(VERSION)

release: clean test build ## Run tests and build release binary
	@echo "Creating release build..."
	@mkdir -p $(BUILD_DIR)/release
	GOOS=linux GOARCH=amd64 $(GO) build $(LDFLAGS) -o $(BUILD_DIR)/release/$(BINARY_NAME)-linux-amd64 $(CMD_DIR)/main.go
	GOOS=linux GOARCH=arm64 $(GO) build $(LDFLAGS) -o $(BUILD_DIR)/release/$(BINARY_NAME)-linux-arm64 $(CMD_DIR)/main.go
	GOOS=darwin GOARCH=amd64 $(GO) build $(LDFLAGS) -o $(BUILD_DIR)/release/$(BINARY_NAME)-darwin-amd64 $(CMD_DIR)/main.go
	GOOS=darwin GOARCH=arm64 $(GO) build $(LDFLAGS) -o $(BUILD_DIR)/release/$(BINARY_NAME)-darwin-arm64 $(CMD_DIR)/main.go
	GOOS=windows GOARCH=amd64 $(GO) build $(LDFLAGS) -o $(BUILD_DIR)/release/$(BINARY_NAME)-windows-amd64.exe $(CMD_DIR)/main.go
	@echo "Release builds complete in $(BUILD_DIR)/release/"
