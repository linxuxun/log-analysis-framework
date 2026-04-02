#!/usr/bin/env python3
"""OPS module - operational log analyzer"""
import sys, json, pathlib

# Accept root_path as command line argument
root = sys.argv[1] if len(sys.argv) > 1 else str(pathlib.Path.cwd())

# Scan for log files recursively
log_files = list(pathlib.Path(root).rglob("*.log"))
ops_logs = [str(f) for f in log_files if 'ops' in str(f).lower()]
if not ops_logs:
    # fallback to logs/ops/
    ops_dir = pathlib.Path(root) / 'logs' / 'ops'
    if ops_dir.exists():
        ops_logs = [str(f) for f in ops_dir.rglob("*.log")]

error_count = warning_count = total_lines = 0
errors, warnings = [], []

for fpath in ops_logs[:5]:
    try:
        with open(fpath, "r", errors="ignore") as fp:
            for line in fp:
                total_lines += 1
                line = line.strip()
                if not line:
                    continue
                l = line.lower()
                if any(k in l for k in ["error", "exception", "fail"]):
                    error_count += 1; errors.append(line[:200])
                elif any(k in l for k in ["warning", "warn"]):
                    warning_count += 1; warnings.append(line[:200])
    except Exception:
        pass

score = max(0, 100 - error_count * 5 - warning_count * 2)
print(json.dumps({
    "status": "ok",
    "message": "OPS log analysis done",
    "module": "OPS",
    "data": {
        "files_analyzed": len(ops_logs[:5]),
        "total_lines": total_lines,
        "errors": error_count,
        "warnings": warning_count,
        "error_list": errors[:10],
        "warning_list": warnings[:10],
        "health_score": score
    }
}, ensure_ascii=False, indent=2))
