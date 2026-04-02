#!/usr/bin/env python3
import sys, json, argparse, logging
from pathlib import Path
logging.basicConfig(level=logging.INFO, format="%(asctime)s [SVC] %(message)s", stream=sys.stderr)
logger = logging.getLogger()

def analyze(log_path):
    err = warn = info = 0
    downs = []
    total = 0
    for lf in list(Path(log_path).rglob("*.log"))[:10]:
        try:
            for line in open(lf, errors="ignore"):
                total += 1
                k = line.lower()
                if "error" in k or "fatal" in k or "exception" in k:
                    err += 1
                elif "warning" in k or "warn" in k:
                    warn += 1
                if "service down" in k or "service stopped" in k:
                    downs.append(line.strip()[:100])
        except: pass
    if err >= 5:
        return {"status": "error", "human_intervention": True,
                "summary": "发现 %d 条服务错误日志" % err,
                "suggestion": "1.查看日志定位原因\n2.检查服务进程状态",
                "details": {"errors": err, "warnings": warn, "scanned": total, "downs": downs[:10]}}
    if err > 0:
        return {"status": "warn", "human_intervention": True,
                "summary": "发现 %d 条错误和 %d 条警告" % (err, warn),
                "suggestion": "关注错误日志，持续新增则处理",
                "details": {"errors": err, "warnings": warn, "scanned": total}}
    return {"status": "health", "human_intervention": False,
            "summary": "核心服务运行正常，共分析 %d 行" % total,
            "suggestion": "", "details": {"scanned": total}}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--log_path", required=True)
    p.add_argument("--output")
    args = p.parse_args()
    r = analyze(args.log_path)
    out = json.dumps(r, ensure_ascii=False, indent=2)
    if args.output:
        open(args.output,"w",encoding="utf-8").write(out)
    else:
        print(out)
