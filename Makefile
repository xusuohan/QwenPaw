# CoPaw Test & Coverage Makefile

.PHONY: test test-unit test-contract test-integration test-channel test-channel-contract coverage-full clean \
       portable portable-windows desktop-macos desktop-linux desktop-windows build-all dist-clean

# Python path
PYTHON := python
PYTEST := python -m pytest

# Default: run all tests
test:
	$(PYTEST) tests/ -v --tb=short -q

# Unit tests only
test-unit:
	$(PYTEST) tests/unit/ -v --tb=short

# Contract tests (interface compliance)
test-contract:
	$(PYTEST) tests/contract/ -v --tb=short

# Integration tests
test-integration:
	$(PYTEST) tests/integration/ -v --tb=short

# Full coverage (all modules)
coverage-full:
	$(PYTEST) tests/unit/ tests/integration/ -v \
		--cov=src/qwenpaw \
		--cov-report=term-missing \
		--cov-report=html

# Check contract coverage for all channels
check-contracts:
	$(PYTHON) scripts/check_channel_contracts.py

# Clean generated files
clean:
	rm -rf htmlcov/ .pytest_cache/
	rm -f coverage.xml coverage-sa.xml .coverage

# Quick check (fast feedback)
quick:
	$(PYTEST) tests/unit/ -x -q --tb=line

# Channel-specific tests
test-channel:
	@echo "Running Channel unit tests..."
	$(PYTEST) tests/unit/channels/ -v --tb=short

test-channel-contract:
	@echo "Running Channel contract tests..."
	$(PYTEST) tests/contract/channels/ -v --tb=short

# BaseChannel core unit tests (optional, not enforced)
test-base-core:
	$(PYTEST) tests/unit/channels/test_base_core.py -v

# Build portable version for current platform (USB plug-and-play)
ifeq ($(OS),Windows_NT)
portable:
	powershell -ExecutionPolicy Bypass -File scripts/pack/build_win_portable.ps1
else
portable:
	bash scripts/pack/build_portable.sh
endif

# Build Windows portable version (PowerShell, run on Windows)
portable-windows:
	powershell -ExecutionPolicy Bypass -File scripts/pack/build_win_portable.ps1

# Build macOS desktop app (.app bundle)
desktop-macos:
	bash scripts/pack/build_macos.sh

# Build Linux portable (Ubuntu/Debian/etc.)
desktop-linux:
	bash scripts/pack/build_linux.sh

# Build Windows portable (PowerShell, run on Windows)
desktop-windows:
	powershell -ExecutionPolicy Bypass -File scripts/pack/build_win.ps1

# Build for all platforms (current platform only, cross-build not supported)
build-all:
	@echo "Building for current platform..."
	@case "$$(uname -s)" in \
		Darwin) $(MAKE) desktop-macos ;; \
		Linux)  $(MAKE) desktop-linux ;; \
		*)      echo "Unsupported platform: $$(uname -s)"; exit 1 ;; \
	esac

# Clean build artifacts
dist-clean:
	rm -rf dist/
	rm -rf .build_venv/
	rm -rf .cache/conda_envs/
