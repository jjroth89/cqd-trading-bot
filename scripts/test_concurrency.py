#!/usr/bin/env python3
"""
Concurrency test for wallet_state.json file locking.
Simulates concurrent read/write operations to verify fcntl.flock protection.
"""

import json
import os
import sys
import time
import threading
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, "/opt/data/cqd-trading-bot")

from core.sandbox_engine import load_wallet, save_wallet, WALLET_FILE, WALLET_LOCK_FILE, wallet_lock

# Test counters
read_count = 0
write_count = 0
errors = []
lock = threading.Lock()

def reader_thread(thread_id: int, iterations: int):
    """Simulate concurrent reads of wallet state."""
    global read_count, errors
    for i in range(iterations):
        try:
            with wallet_lock(exclusive=False):  # Shared lock for reads
                wallet = load_wallet()
                # Simulate some processing
                balance = wallet.get("balance_usdt", 0)
                positions = len(wallet.get("open_positions", {}))
            with lock:
                read_count += 1
            time.sleep(0.001)  # Small delay
        except Exception as e:
            with lock:
                errors.append(f"Reader {thread_id} iteration {i}: {e}")

def writer_thread(thread_id: int, iterations: int):
    """Simulate concurrent writes to wallet state."""
    global write_count, errors
    for i in range(iterations):
        try:
            with wallet_lock(exclusive=True):  # Exclusive lock for writes
                wallet = load_wallet()
                # Simulate a small change
                wallet["balance_usdt"] = round(wallet.get("balance_usdt", 10000.0) - 0.01, 2)
                save_wallet(wallet)
            with lock:
                write_count += 1
            time.sleep(0.001)  # Small delay
        except Exception as e:
            with lock:
                errors.append(f"Writer {thread_id} iteration {i}: {e}")

def test_concurrent_access():
    """Run concurrent readers and writers."""
    print("Starting concurrency test...")
    print(f"Wallet file: {WALLET_FILE}")
    print(f"Lock file: {WALLET_LOCK_FILE}")
    
    # Ensure wallet exists with initial state
    initial_wallet = {
        "balance_usdt": 10000.0,
        "open_positions": {},
        "trade_history": []
    }
    with wallet_lock(exclusive=True):
        save_wallet(initial_wallet)
    
    threads = []
    num_readers = 5
    num_writers = 3
    iterations = 20
    
    # Start reader threads
    for i in range(num_readers):
        t = threading.Thread(target=reader_thread, args=(i, iterations))
        threads.append(t)
        t.start()
    
    # Start writer threads
    for i in range(num_writers):
        t = threading.Thread(target=writer_thread, args=(i, iterations))
        threads.append(t)
        t.start()
    
    # Wait for all threads
    for t in threads:
        t.join()
    
    # Verify final state
    with wallet_lock(exclusive=False):
        final_wallet = load_wallet()
    
    expected_balance = 10000.0 - (num_writers * iterations * 0.01)
    actual_balance = final_wallet.get("balance_usdt", 0)
    
    print(f"\nResults:")
    print(f"  Successful reads: {read_count} (expected {num_readers * iterations})")
    print(f"  Successful writes: {write_count} (expected {num_writers * iterations})")
    print(f"  Errors: {len(errors)}")
    print(f"  Expected final balance: {expected_balance:.2f}")
    print(f"  Actual final balance: {actual_balance:.2f}")
    
    if errors:
        print("\nErrors encountered:")
        for e in errors[:5]:  # Show first 5 errors
            print(f"  {e}")
        return False
    
    # Check balance is close to expected (floating point)
    if abs(actual_balance - expected_balance) < 0.02:
        print("\n✅ CONCURRENCY TEST PASSED - File locking working correctly")
        return True
    else:
        print(f"\n❌ CONCURRENCY TEST FAILED - Balance mismatch")
        return False

if __name__ == "__main__":
    success = test_concurrent_access()
    sys.exit(0 if success else 1)