#!/usr/bin/env python3
"""Comprehensive test runner for the ODTA project."""

import sys
import subprocess
import argparse
from pathlib import Path
import time


def run_command(cmd, description):
    """Run a command and report results."""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print('='*60)
    
    start_time = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    duration = time.time() - start_time
    
    if result.returncode == 0:
        print(f"✅ {description} passed in {duration:.2f}s")
    else:
        print(f"❌ {description} failed")
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
    
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Run ODTA test suite")
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument("--integration", action="store_true", help="Run integration tests only")
    parser.add_argument("--system", action="store_true", help="Run system tests only")
    parser.add_argument("--performance", action="store_true", help="Run performance tests only")
    parser.add_argument("--security", action="store_true", help="Run security tests only")
    parser.add_argument("--all", action="store_true", help="Run all tests (default)")
    parser.add_argument("--coverage", action="store_true", help="Generate coverage report")
    parser.add_argument("--lint", action="store_true", help="Run linting checks")
    parser.add_argument("--type-check", action="store_true", help="Run type checking")
    parser.add_argument("--quick", action="store_true", help="Run quick tests only (no slow/stress)")
    
    args = parser.parse_args()
    
    # If no specific test type selected, run all
    if not any([args.unit, args.integration, args.system, args.performance, args.security]):
        args.all = True
    
    success = True
    
    # Linting
    if args.lint or args.all:
        success &= run_command(
            ["black", "--check", "src/", "tests/", "dashboard.py"],
            "Black formatting check"
        )
        success &= run_command(
            ["flake8", "src/", "tests/", "dashboard.py"],
            "Flake8 linting"
        )
    
    # Type checking
    if args.type_check or args.all:
        success &= run_command(
            ["mypy", "src/", "--ignore-missing-imports"],
            "MyPy type checking"
        )
    
    # Unit tests
    if args.unit or args.all:
        cmd = ["pytest", "tests/unit/", "-v"]
        if args.coverage:
            cmd.extend(["--cov=src", "--cov-report=term-missing"])
        if args.quick:
            cmd.extend(["-m", "not slow"])
        success &= run_command(cmd, "Unit tests")
    
    # Integration tests  
    if args.integration or args.all:
        cmd = ["pytest", "tests/integration/", "-v"]
        if args.coverage:
            cmd.extend(["--cov=src", "--cov-append"])
        if args.quick:
            cmd.extend(["-m", "not slow"])
        success &= run_command(cmd, "Integration tests")
    
    # System tests
    if args.system or args.all:
        cmd = ["pytest", "tests/system/", "-v"]
        if args.coverage:
            cmd.extend(["--cov=src", "--cov-append"])
        if args.quick:
            cmd.extend(["-m", "not slow"])
        success &= run_command(cmd, "System tests")
    
    # Performance tests
    if args.performance and not args.quick:
        cmd = ["pytest", "tests/performance/", "-v", "-m", "benchmark"]
        success &= run_command(cmd, "Performance benchmarks")
        
        if not args.quick:
            cmd = ["pytest", "tests/performance/", "-v", "-m", "stress"]
            success &= run_command(cmd, "Stress tests")
    
    # Security tests
    if args.security or args.all:
        cmd = ["pytest", "tests/security/", "-v"]
        success &= run_command(cmd, "Security tests")
        
        # Run additional security checks
        success &= run_command(
            ["bandit", "-r", "src/", "-f", "json", "-o", "bandit-report.json"],
            "Bandit security scan"
        )
    
    # Generate final coverage report
    if args.coverage:
        run_command(
            ["coverage", "html"],
            "Generate HTML coverage report"
        )
        run_command(
            ["coverage", "report"],
            "Coverage summary"
        )
        print("\n📊 Coverage report available at: htmlcov/index.html")
    
    # Summary
    print("\n" + "="*60)
    if success:
        print("✅ All tests passed!")
    else:
        print("❌ Some tests failed. Please check the output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()