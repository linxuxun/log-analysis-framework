"""
报告生成器
"""
import json
from datetime import datetime
from typing import List, Dict, Any


def generate_report(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    生成汇总报告

    Args:
        results: 各模块执行结果列表

    Returns:
        汇总报告字典
    """
    total = len(results)
    success = sum(1 for r in results if r.get("status") == "success")
    failed = sum(1 for r in results if r.get("status") == "failed")
    errors = sum(1 for r in results if r.get("status") == "error")
    timeouts = sum(1 for r in results if r.get("status") == "timeout")

    report = {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total": total,
            "success": success,
            "failed": failed,
            "error": errors,
            "timeout": timeouts,
            "success_rate": f"{(success/total*100):.1f}%" if total > 0 else "0%"
        },
        "modules": []
    }

    for r in results:
        module_report = {
            "module": r.get("module", "unknown"),
            "status": r.get("status", "unknown"),
            "data": r.get("data"),
            "error": r.get("error"),
        }
        report["modules"].append(module_report)

    return report


def write_report(report: Dict[str, Any], output_path: str) -> None:
    """将报告写入文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
