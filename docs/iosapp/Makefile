.PHONY: build clean lint lint-fix test test-all check help prepare-build-dirs storage-up storage-status

# Default target
.DEFAULT_GOAL := help

# Variables
PROJECT := foqos.xcodeproj
SCHEME := foqos
CONFIGURATION := Debug
DESTINATION := generic/platform=iOS Simulator
# Pick whatever simulator this Mac actually has. Hardcoding a model name means
# `make test` dies with "Unable to find a device matching the provided
# destination specifier" on any machine without that exact device.
SIM_NAME ?= $(shell xcrun simctl list devices available -j 2>/dev/null | python3 -c "import json,sys;d=json.load(sys.stdin)['devices'];print(next((v['name'] for r in d for v in d[r] if v.get('isAvailable')),'iPhone 17'))" 2>/dev/null || echo 'iPhone 17')
TEST_DESTINATION ?= platform=iOS Simulator,name=$(SIM_NAME)
UNIT_TEST_TARGET ?= foqosTests
XCODE_APP_DEVELOPER_DIR := /Applications/Xcode.app/Contents/Developer
DEVELOPER_DIR ?= $(if $(wildcard $(XCODE_APP_DEVELOPER_DIR)),$(XCODE_APP_DEVELOPER_DIR),$(shell xcode-select -p))
XCODEBUILD := DEVELOPER_DIR='$(DEVELOPER_DIR)' xcodebuild
SWIFT_FORMAT := DEVELOPER_DIR='$(DEVELOPER_DIR)' xcrun swift-format
EXTERNAL_DEVELOPER_ROOT ?= $(HOME)/Library/Developer/ArgonSSD/Developer
LOCAL_DEVELOPER_ROOT := $(HOME)/Library/Developer/Xcode
ACTIVE_DEVELOPER_ROOT := $(if $(wildcard $(EXTERNAL_DEVELOPER_ROOT)),$(EXTERNAL_DEVELOPER_ROOT),$(LOCAL_DEVELOPER_ROOT))
DERIVED_DATA_PATH ?= $(ACTIVE_DEVELOPER_ROOT)/DerivedData/argon-ios
RESULT_BUNDLE_DIR ?= $(ACTIVE_DEVELOPER_ROOT)/TestResults
RESULT_BUNDLE_PATH ?= $(RESULT_BUNDLE_DIR)/foqos-$(shell date +%Y%m%d-%H%M%S).xcresult
XCODEBUILD_STORAGE_FLAGS := -derivedDataPath '$(DERIVED_DATA_PATH)'
TEST_RESOURCE_FLAGS := -parallel-testing-enabled NO -maximum-concurrent-test-simulator-destinations 1

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

prepare-build-dirs:
	@mkdir -p '$(DERIVED_DATA_PATH)' '$(RESULT_BUNDLE_DIR)'

storage-up: ## Attach SSD-backed simulator and XCTest storage
	@./scripts/ensure-simulator-storage.sh

storage-status: ## Show where simulator and build data are stored
	@echo "Simulator: $$(diskutil info '$(HOME)/Library/Developer/CoreSimulator/Devices' 2>/dev/null | awk -F: '/Volume Name|Protocol|Mount Point/ {gsub(/^[ \t]+/, "", $$2); printf "%s%s", sep, $$2; sep=" · "}')"
	@echo "DerivedData: $(DERIVED_DATA_PATH)"
	@echo "Test results: $(RESULT_BUNDLE_DIR)"

build: prepare-build-dirs ## Build the project
	$(XCODEBUILD) -project $(PROJECT) -scheme $(SCHEME) -configuration $(CONFIGURATION) -destination '$(DESTINATION)' $(XCODEBUILD_STORAGE_FLAGS) build

clean: ## Clean build artifacts
	$(XCODEBUILD) -project $(PROJECT) -scheme $(SCHEME) $(XCODEBUILD_STORAGE_FLAGS) clean

lint: ## Check Swift formatting
	$(SWIFT_FORMAT) lint --recursive .

lint-fix: ## Fix Swift formatting issues
	$(SWIFT_FORMAT) format --recursive --in-place .

test: prepare-build-dirs ## Run unit tests without spawning parallel simulator clones
	$(XCODEBUILD) -project $(PROJECT) -scheme $(SCHEME) -configuration $(CONFIGURATION) -destination '$(TEST_DESTINATION)' $(XCODEBUILD_STORAGE_FLAGS) -resultBundlePath '$(RESULT_BUNDLE_PATH)' $(TEST_RESOURCE_FLAGS) test -only-testing:$(UNIT_TEST_TARGET)

test-all: prepare-build-dirs ## Run all tests, including UI tests
	$(XCODEBUILD) -project $(PROJECT) -scheme $(SCHEME) -configuration $(CONFIGURATION) -destination '$(TEST_DESTINATION)' $(XCODEBUILD_STORAGE_FLAGS) -resultBundlePath '$(RESULT_BUNDLE_PATH)' test

check: ## Run lint and build
	$(MAKE) lint
	$(MAKE) build
