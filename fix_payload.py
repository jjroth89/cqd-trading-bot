#!/usr/bin/env python3
import json
from pathlib import Path

# Read the payload
with open("/tmp/cqd_trigger_OPN_USDT.json") as f:
    payload = json.load(f)

# Add action field for sandbox execution
payload["action"] = "EXECUTE"

# Write modified payload
Path("/tmp/cqd_trigger_OPN_USDT_exec.json").write_text(json.dumps(payload, indent=2))
print("Payload updated with action field")