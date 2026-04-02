#!/usr/bin/env python3
"""
OPS模块 - 系统运维日志分析

支持解析7种告警类型:
  1. hdNetMasterCtrlFaultAlarmHandle  change(0->1)触发 / change(1->0)恢复
  2. hdNetProcCardFaultAlarm          restore 0触发 / restore 1恢复
  3. hdNetProcCtrlFaultAlarm         fault alarm succ.触发
  4. hdNetProcSlowLinkAlarm          subhealth Alarm未恢复 / 无subhealth Alarm已恢复
  5. hdProcRDiskAlarm                isRestore:0触发 / isRestore:1恢复
  6. hdProcUDiskAlarm                isRestore:0触发 / isRestore:1恢复
  7. hdProcEncAlarm                  isRestore:0触发 / isRestore:1恢复
"""
import sys, os, json, argparse, logging, re
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OPS] %(levelname)s %(message)s",
    stream=sys.stderr)
logger = logging.getLogger("ops_analyzer")

ALARM_TYPES = [
    "hdNetMasterCtrlFaultAlarmHandle",
    "hdNetProcCardFaultAlarm",
    "hdNetProcCtrlFaultAlarm",
    "hdNetProcSlowLinkAlarm",
    "hdProcRDiskAlarm",
    "hdProcUDiskAlarm",
    "hdProcEncAlarm",
]


def parse_alarm(line):
    """识别告警类型并提取字段，返回 dict 或 None。"""
    hit = None
    for atype in ALARM_TYPES:
        if atype in line:
            hit = atype
            break
    if hit is None:
        return None

    result = {"alarm_type": hit, "raw": line[:120].strip()}

    # ── 1. hdNetMasterCtrlFaultAlarmHandle ──────────────
    if hit == "hdNetMasterCtrlFaultAlarmHandle":
        m = re.search(r"change\s*\(\s*(\d+)\s*->\s*(\d+)\s*\)", line)
        if m:
            old, new = int(m.group(1)), int(m.group(2))
            result["from_val"] = old
            result["to_val"]   = new
            # 0->1 触发，1->0 恢复
            result["is_restore"] = 0 if new == 1 else 1
        else:
            result["is_restore"] = 0

    # ── 2. hdNetProcCardFaultAlarm ──────────────────────
    elif hit == "hdNetProcCardFaultAlarm":
        m = re.search(r"\brestore\s+(\d)\b", line)
        if m:
            result["is_restore"] = int(m.group(1))  # 0=未恢复 1=恢复
        else:
            result["is_restore"] = 0

    # ── 3. hdNetProcCtrlFaultAlarm ──────────────────────
    elif hit == "hdNetProcCtrlFaultAlarm":
        result["is_restore"] = 0  # fault alarm succ. → 触发

    # ── 4. hdNetProcSlowLinkAlarm ────────────────────────
    elif hit == "hdNetProcSlowLinkAlarm":
        # subhealth Alarm → 未恢复（warn）
        # 无 subhealth Alarm → 已恢复（health）
        result["is_restore"] = 0 if "subhealth Alarm" in line else 1

    # ── 5-7. isRestore 系列（hdProcRDiskAlarm / hdProcUDiskAlarm / hdProcEncAlarm）─
    else:
        p = line.find("isRestore:")
        if p < 0:
            return None
        val = line[p + 10:p + 11]
        result["is_restore"] = int(val) if val.isdigit() else 0
        # diskId / encId 提取
        pd = line.find("diskId:") if hit != "hdProcEncAlarm" else line.find("encId:")
        if pd >= 0 and pd < p:
            ed = line.find(" ", pd + (8 if "disk" in hit else 7))
            end = ed if ed > 0 and ed < p else p
            key = "disk_id" if "disk" in hit else "enc_id"
            result[key] = line[pd + (8 if "disk" in hit else 7):end].strip()
        # sn 提取
        ps = line.find("sn:")
        if ps >= 0 and ps < (pd if pd >= 0 else p):
            es = line.find(" ", ps + 3)
            end = es if es > 0 and es < p else p
            result["sn"] = line[ps + 3:end].strip()

    return result


def analyze_ops(log_path):
    alarm_list = []
    total = err = warn = 0

    log_files = list(Path(log_path).rglob("*.log"))
    logger.info("扫描 %d 个日志文件", len(log_files))

    for lf in log_files[:30]:
        try:
            with open(lf, "r", errors="ignore") as f:
                for line in f:
                    total += 1
                    s = line.strip()
                    if not s:
                        continue
                    alarm = parse_alarm(s)
                    if alarm:
                        alarm["file"] = lf.name
                        alarm_list.append(alarm)
                        if alarm["is_restore"] == 0:
                            err += 1
                        else:
                            warn += 1
                    elif "ERROR" in s.upper():
                        err += 1
                    elif "WARNING" in s.upper():
                        warn += 1
        except Exception as e:
            logger.warning("读取失败 %s: %s", lf, e)

    # ── 按类型分组 ─────────────────────────────────────
    by_type = {}
    for a in alarm_list:
        t = a["alarm_type"]
        by_type.setdefault(t, {"active": [], "restored": []})
        bucket = by_type[t]["active" if a["is_restore"] == 0 else "restored"]
        bucket.append(a)

    active_all   = [a for a in alarm_list if a["is_restore"] == 0]
    restored_all = [a for a in alarm_list if a["is_restore"] == 1]

    summaries, suggestions = [], []

    for atype, buckets in by_type.items():
        active, restored = buckets["active"], buckets["restored"]
        if active:
            disks = sorted({
                a.get(x, "?")
                for a in active
                for x in ["disk_id", "enc_id"]
                if a.get(x) and a.get(x) != "?"
            })
            disk_str = f"，涉及 {', '.join(disks)}" if disks else ""
            summaries.append(f"{atype}：{len(active)}条未恢复{disk_str}")
            suggestions.append(_suggestion_for(atype, active))
        elif restored:
            summaries.append(f"{atype}：{len(restored)}条已恢复")

    if summaries:
        status = "error"; hi = True
        summary = "；".join(summaries)
        suggestion = "\n".join(suggestions)
    else:
        status = "health"; hi = False
        summary = "OPS日志分析完成，未发现任何告警，系统运行正常"
        suggestion = ""

    logger.info("完成: total=%d active=%d restored=%d types=%s",
                total, len(active_all), len(restored_all), list(by_type.keys()))

    return {
        "status": status,
        "human_intervention": hi,
        "summary": summary,
        "suggestion": suggestion,
        "details": {
            "files_scanned": len(log_files),
            "total_lines": total,
            "active_alarms":  len(active_all),
            "restored_alarms": len(restored_all),
            "by_type": {t: {"active": len(b["active"]), "restored": len(b["restored"])}
                        for t, b in by_type.items()},
            "alarm_list": active_all[:30] if active_all else restored_all[:10],
            "log_files": [str(f.relative_to(log_path)) for f in log_files[:10]]
        }
    }


def _suggestion_for(atype, active):
    if atype == "hdNetMasterCtrlFaultAlarmHandle":
        slots = [a.get("to_val","?") for a in active]
        return f"检查主控模块状态变化（slots: {', '.join(map(str,slots))}），确认是否故障或需重启"
    elif atype == "hdNetProcCardFaultAlarm":
        return "检查网卡/业务卡状态，确认是否需要更换或重启相关进程"
    elif atype == "hdNetProcCtrlFaultAlarm":
        return "查看Ctrl进程告警详情，确认故障原因并及时处理"
    elif atype == "hdNetProcSlowLinkAlarm":
        return "检查链路质量，确认是否存在慢链路或网络抖动"
    elif atype in ("hdProcRDiskAlarm", "hdProcUDiskAlarm"):
        disks = sorted({a.get("disk_id","?") for a in active if a.get("disk_id")})
        return f"检查磁盘物理连接（diskId: {', '.join(disks)}），确认硬盘是否松动或故障"
    elif atype == "hdProcEncAlarm":
        return "检查加密引擎状态，确认加密模块是否异常"
    return "及时处理告警"


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="OPS运维日志分析")
    p.add_argument("--log_path", required=True)
    p.add_argument("--output")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not os.path.isdir(args.log_path):
        print(json.dumps({
            "status": "error", "human_intervention": True,
            "summary": "日志目录不存在: %s" % args.log_path,
            "suggestion": "请检查日志路径"
        }, ensure_ascii=False))
        sys.exit(1)

    result = analyze_ops(args.log_path)
    out = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        logger.info("已写入: %s", args.output)
    else:
        print(out)
