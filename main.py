#!/usr/bin/env python3
"""Log Analysis Framework - Main Entry"""
import subprocess, json, sys, argparse
from typing import Dict, Any

from config_parser import parse_config, ConfigError, SchemaValidationError, validate_schema
from executor import execute_command
from reporter import generate_report, write_report
from log_collector import collect_logs_by_pattern, to_node_list_format
from framework import utils

def main():
    args = parse_args()
    try:
        config = parse_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    # Pre-collect log files for each module
    all_logs = {}
    for mc in config.modules:
        raw = collect_logs_by_pattern(args.root_path, mc.patterns) if mc.patterns else []
        all_logs[mc.name] = to_node_list_format(raw)

    utils.set_all_modules_log_files(all_logs)
    utils.set_root_path(args.root_path)

    # Filter modules by --module flag
    if args.module:
        modules_run = [m for m in config.modules if m.name == args.module]
        if not modules_run:
            print(f"Module not found: {args.module}", file=sys.stderr)
            print(f"Available: {[m.name for m in config.modules]}", file=sys.stderr)
            sys.exit(1)
    else:
        modules_run = config.modules

    results = []
    for mc in modules_run:
        r = execute_module(mc, args.root_path)
        results.append(r)
        print(f"[{r['status']}] {r['module']}")

    report = generate_report(results)
    if args.output:
        write_report(report, args.output)
        print(f"Report: {args.output}")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))


def execute_module(mc, root_path: str) -> Dict[str, Any]:
    """Execute a single module script and parse its JSON output"""
    print(f"Running: {mc.name}")
    # Pass root_path as argument to script
    script_cmd = f"{mc.script} {root_path}"
    try:
        result = execute_command(script_cmd, mc.timeout)
        if result.returncode not in (0, None):
            return {
                "module": mc.name, "status": "failed",
                "error": f"exit {result.returncode}",
                "stderr": result.stderr[:200] if result.stderr else ""
            }
        try:
            data = json.loads(result.stdout)
            # Scripts output: {status, message, module, data: {...}}
            # Unwrap for clean report
            inner = data.get("data", {})
            return {
                "module": mc.name, "status": "success",
                "data": {
                    "status": data.get("status", "ok"),
                    "message": data.get("message", ""),
                    "module": data.get("module", mc.name),
                    "data": inner
                }
            }
        except json.JSONDecodeError as e:
            return {
                "module": mc.name, "status": "error",
                "error": f"JSON parse failed: {e}\nOutput: {result.stdout[:200]}"
            }
    except subprocess.TimeoutExpired:
        return {"module": mc.name, "status": "timeout", "error": f"timeout after {mc.timeout}s"}
    except Exception as e:
        return {"module": mc.name, "status": "error", "error": str(e)}


def parse_args():
    p = argparse.ArgumentParser(description="Log Analysis Framework")
    p.add_argument("--root_path", required=True,
                   help="Root path of log directory")
    p.add_argument("--config", default="config/tools_analysis.xml",
                   help="Config XML path")
    p.add_argument("--module",
                   help="Run specific module only (e.g. OPS, DISK, NET)")
    p.add_argument("--output",
                   help="Output report to file")
    return p.parse_args()


if __name__ == "__main__":
    main()
