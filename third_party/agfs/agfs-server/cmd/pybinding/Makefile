.PHONY: all build-macos build-linux build-windows clean install test

GO=go
BUILD_DIR=build
LIB_NAME=libagfsbinding
BIN_DIR=../../../bin

all: build-macos

build-macos:
	@echo "Building shared library for macOS..."
	@mkdir -p $(BUILD_DIR)
	@mkdir -p $(BIN_DIR)
	CGO_ENABLED=1 $(GO) build -buildmode=c-shared -o $(BUILD_DIR)/$(LIB_NAME).dylib .
	@cp $(BUILD_DIR)/$(LIB_NAME).dylib $(BIN_DIR)/
	@echo "Build complete: $(BIN_DIR)/$(LIB_NAME).dylib"

build-linux:
	@echo "Building shared library for Linux..."
	@mkdir -p $(BUILD_DIR)
	@mkdir -p $(BIN_DIR)
	CGO_ENABLED=1 $(GO) build -buildmode=c-shared -o $(BUILD_DIR)/$(LIB_NAME).so .
	@cp $(BUILD_DIR)/$(LIB_NAME).so $(BIN_DIR)/
	@echo "Build complete: $(BIN_DIR)/$(LIB_NAME).so"

build-windows:
	@echo "Building shared library for Windows..."
	@mkdir -p $(BUILD_DIR)
	@mkdir -p $(BIN_DIR)
	CC=x86_64-w64-mingw32-gcc CXX=x86_64-w64-mingw32-g++ AR=x86_64-w64-mingw32-ar GOOS=windows GOARCH=amd64 CGO_ENABLED=1 $(GO) build -buildmode=c-shared -o $(BUILD_DIR)/$(LIB_NAME).dll .
	@cp $(BUILD_DIR)/$(LIB_NAME).dll $(BIN_DIR)/
	@echo "Build complete: $(BIN_DIR)/$(LIB_NAME).dll"

clean:
	@echo "Cleaning..."
	@rm -rf $(BUILD_DIR)
	@rm -f *.so *.dylib *.dll *.h
	@rm -f $(BIN_DIR)/$(LIB_NAME).so $(BIN_DIR)/$(LIB_NAME).dylib $(BIN_DIR)/$(LIB_NAME).dll
	@echo "Clean complete"

install: build-macos
	@echo "Installing..."
	@cp $(BUILD_DIR)/$(LIB_NAME).dylib /usr/local/lib/ 2>/dev/null || true
	@echo "Install complete"

test:
	$(GO) test -v ./...
