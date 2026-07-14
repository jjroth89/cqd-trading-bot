#!/usr/bin/env python3
"""
Concurrency test for wallet_state.json file locking.
Verifies that fcntl.flock + atomic read-modify-write via modify_wallet()
prevents lost updates under concurrent threads.
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, "/opt/data/cqd-trading-bot")

from core.sandbox_engine import (
    load_wallet,
    save_wallet,
    modify_wallet,
    WALLET_FILE,
)

# Test counters
read_count = 0
write_count = 0
errors = []
counter_lock = threading.Lock()


def reader_thread(thread_id: int, iterations: int):
    """Simulate concurrent reads of wallet state."""
    global read_count, errors
    for _ in range(iterations):
        try:
            wallet = load_wallet()  # handles its own locking
            _ = wallet.get("balance_usdt", 0)
            _ = len(wallet.get("open_positions", {}))
            with counter_lock:
                read_count += 1
        except Exception as e:
            with counter_lock:
                errors.append(f"Reader {thread_id}: {e}")


def writer_thread(thread_id: int, iterations: int):
    """
    Concurrent writes using modify_wallet() — the atomic transaction API.
    Each decrement is a full read-modify-write under a single lock acquisition.
    """
    global write_count, errors
    for _ in range(iterations):
        try:
            def decrement(wallet):
                wallet["balance_usdt"] = round(wallet.get("balance_usdt", 10000.0) - 0.01, 2)
                return wallet, True

            modify_wallet(decrement)
            with counter_lock:
                write_count += 1
        except Exception as e:
            with counter_lock:
                errors.append(f"Writer {thread_id}: {e}")


def test_concurrent_access():
    print("Starting concurrency test (using modify_wallet atomic transactions)...")
    print(f"Wallet file: {WALLET_FILE}")

    # Reset to known state
    initial = {"balance_usdt": 10000.0, "open_positions": {}, "trade_history": []}
    save_wallet(initial)

    threads = []
    num_readers = 4
    num_writers = 3
    iterations = 15

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
        t.join(timeout=60)

    alive = [t for t in threads if t.is_alive()]
    if alive:
        print(f"WARNING: {len(alive)} threads still alive after timeout")
        return False

    # Verify final state
    final_wallet = load_wallet()
    expected_balance = round(10000.0 - (num_writers * iterations * 0.01), 2)
    actual_balance = round(final_wallet.get("balance_usdt", 0.0), 2)

    print(f"\nResults:")
    print(f"  Successful reads:  {read_count} (expected {num_readers * iterations})")
    print(f"  Successful writes: {write_count} (expected {num_writers * iterations})")
    print(f"  Errors: {len(errors)}")
    print(f"  Expected final balance: {expected_balance:.2f}")
    print(f"  Actual final balance:   {actual_balance:.2f}")

    if errors:
        print("\nErrors:")
        for e in errors[:5]:
            print(f"  {e}")
        return False

    if actual_balance == expected_balance:
        print(f"\n✅ CONCURRENCY TEST PASSED — no lost updates")
        return True
    else:
        print(f"\n❌ CONCURRENCY TEST FAILED — balance mismatch (diff={actual_balance - expected_balance:.2f})")
        return False


if __name__ == "__main__":
    success = test_concurrent_access()
    sys.exit(0 if success else 1)
