# QwenPaw Makefile

.PHONY: test test-unit test-contract test-integration test-channel test-channel-contract coverage-full clean
.PHONY: portable portable-macos portable-linux portable-windows portable-clean

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

# ============================================================================
# Portable (U盘便携版) 构建
# ============================================================================

# 构建当前平台的便携版
portable:
	@echo "== Building portable version for current platform =="
	bash ./scripts/pack/build_portable.sh

# 构建 macOS 便携版
portable-macos:
	@echo "== Building macOS portable version =="
	bash ./scripts/pack/build_macos.sh
	@mkdir -p dist/QwenPaw-Portable/macOS
	@cp -R dist/QwenPaw.app dist/QwenPaw-Portable/macOS/
	@echo "== macOS portable built at dist/QwenPaw-Portable/macOS/ =="

# 构建 Linux 便携版
portable-linux:
	@echo "== Building Linux portable version =="
	bash ./scripts/pack/build_linux.sh
	@mkdir -p dist/QwenPaw-Portable/linux
	@cp -R dist/linux/* dist/QwenPaw-Portable/linux/
	@echo "== Linux portable built at dist/QwenPaw-Portable/linux/ =="

# 构建 Windows 便携版 (需要在 Windows 上运行)
portable-windows:
	@echo "== Windows portable must be built on Windows =="
	@echo "Run: .\scripts\pack\build_win.ps1"

# 清理便携版构建产物
portable-clean:
	@echo "== Cleaning portable build artifacts =="
	rm -rf dist/QwenPaw-Portable
	rm -rf dist/QwenPaw.app
	rm -rf dist/linux
	rm -rf dist/win-unpacked
	rm -f dist/qwenpaw-env.tar.gz
	rm -f dist/qwenpaw-env.zip
	@echo "== Portable build artifacts cleaned =="
