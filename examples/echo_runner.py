"""Example stand-in runner; NOT a real harness or an LLM."""
import json
import sys
prompt = sys.stdin.read()
print(json.dumps({"response": "Mutex lock explanation" if "mutex" in prompt.lower() else "Concurrency bottleneck and contention report", "tool_calls": []}))
