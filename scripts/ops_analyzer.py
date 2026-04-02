#!/usr/bin/env python3
"""
OPS模块 - 系统运维日志分析

支持解析5种告警类型:
  1. HD_ALARM_DISK_SINGLE_LINK    — isRestore:0/1
  2. hdNetMasterCtrlFaultAlarmHandle — change(N -> M)
  3. hdNetProcCardFaultAlarm      — restore 0 / restore 1
  4. hdNetProcCtrlFaultAlarm      — fault alarm succ.
  5. hdNetProcSlowLinkAlarm       — restore Alarm

状态判断（每种告警）:
  - error: 告警触发条件满足（未恢复）
  - warn:  告警已恢复
  - health: 无任何告警记录
"""
import sys, os, json, argparse, logging, re
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OPS] %(levelname)s %(message)s",
    stream=sys.stderr)
logger = logging.getLogger("ops_analyzer")

# ── 告警类型注册表 ──────────────────────────────────────────
ALARM_TYPES = [
    "HD_ALARM_DISK_SINGLE_LINK",
    "hdNetMasterCtrlFaultAlarmHandle",
    "hdNetProcCardFaultAlarm",
    "hdNetProcCtrlFaultAlarm",
    "hdNetProcSlowLinkAlarm",
]


# ── 解析器：按类型 dispatch ────────────────────────────────

def parse_alarm(line):
    """
    识别行中的告警类型，提取类型名 + 恢复标志 + 扩展字段。
    返回 dict 或 None。
    """
    hit = None
    for atype in ALARM_TYPES:
        if atype in line:
            hit = atype
            break
    if hit is None:
        return None

    result = {"alarm_type": hit, "raw": line[:120].strip()}

    # ── 1. HD_ALARM_DISK_SINGLE_LINK ────────────────────
    if hit == "HD_ALARM_DISK_SINGLE_LINK":
        p = line.find("isRestore:")
        if p < 0:
            return None
        val = line[p + 10:p + 11]
        result["is_restore"] = int(val) if val.isdigit() else 0
        # diskId
        pd = line.find("diskId:")
        if 0 <= pd < p:
            ed = line.find(" ", pd + 8)
            result["disk_id"] = line[pd + 8:ed if ed > 0 else p].strip()
        else:
            result["disk_id"] = ""
        # sn
        ps = line.find("sn:")
        if 0 <= ps < pd:
            es = line.find(" ", ps + 3)
            result["sn"] = line[ps + 3:es if es > 0 else pd].strip()
        else:
            result["sn"] = ""

    # ── 2. hdNetMasterCtrlFaultAlarmHandle ───────────────
    elif hit == "hdNetMasterCtrlFaultAlarmHandle":
        m = re.search(r"change\s*\(\s*(\d+)\s*->\s*(\d+)\s*\)", line)
        if m:
            old, new = int(m.group(1)), int(m.group(2))
            result["from_val"] = old
            result["to_val"]   = new
            # 1 表示故障触发（0->1）；0 表示恢复（1->0）
            result["is_restore"] = 0 if new == 1 else 1
        else:
            result["is_restore"] = 0
        result["desc"] = _bracket_val(line, "change") or ""

    # ── 3. hdNetProcCardFaultAlarm ────────────────────────
    elif hit == "hdNetProcCardFaultAlarm":
        # "restore 0" → 未恢复；"restore 1" → 已恢复
        m = re.search(r"\brestore\s+(\d)\b", line)
        if m:
            result["is_restore"] = int(m.group(1))
        else:
            result["is_restore"] = 0
        result["desc"] = _bracket_val(line, "hdNetProcCardFaultAlarm") or ""

    # ── 4. hdNetProcCtrlFaultAlarm ────────────────────────
    elif hit == "hdNetProcCtrlFaultAlarm":
        # "fault alarm succ." → 告警成功触发（is_restore=0）
        # 没有任何 restore 标记时默认是触发
        result["is_restore"] = 0
        result["desc"] = _bracket_val(line, "hdNetProcCtrlFaultAlarm") or ""

    # ── 5. hdNetProcSlowLinkAlarm ────────────────────────
    elif hit == "hdNetProcSlowLinkAlarm":
        # "restore Alarm" → 已恢复（is_restore=1）；否则触发（is_restore=0）
        result["is_restore"] = 1 if "restore Alarm" in line else 0
        result["desc"] = _bracket_val(line, "hdNetProcSlowLinkAlarm") or ""

    return result


def _bracket_val(line, keyword):
    """提取 [keyword:xxx] 格式的值，返回 None 表示未找到。"""
    m = re.search(r"\[" + re.escape(keyword) + r":([^\]]+)\]", line)
    return m.group(1).strip() if m else None


# ── 分析主函数 ───────────────────────────────────────────

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
        bucket = by_type[t]["active"] if a["is_restore"] == 0 else by_type[t]["restored"]
        bucket.append(a)

    # ── 生成摘要 ──────────────────────────────────────
    active_all   = [a for a in alarm_list if a["is_restore"] == 0]
    restored_all = [a for a in alarm_list if a["is_restore"] == 1]

    summaries = []
    suggestions = []
    for atype, buckets in by_type.items():
        active   = buckets["active"]
        restored = buckets["restored"]
        if active:
            disk_ids = sorted({
                a.get("disk_id", "?")
                for a in active
                if a.get("disk_id") and a["disk_id"] != "?"
            })
            extra = ""
            if atype == "hdNetMasterCtrlFaultAlarmHandle":
                changes = [f"{a.get('from_val')}→{a.get('to_val')}" for a in active]
                extra = f"（状态变化: {', '.join(changes)}）"
            elif atype == "hdNetProcCardFaultAlarm":
                extra = f"（{len(active)}次未恢复）"
            elif atype == "hdNetProcCtrlFaultAlarm":
                extra = f"（{len(active)}次触发）"
            elif atype == "hdNetProcSlowLinkAlarm":
                extra = f"（{len(active)}次未恢复）"
            disk_str = f"，涉及磁盘 {', '.join(disk_ids)}" if disk_ids else ""
            summaries.append(
                f"{atype}：{len(active)}条未恢复{extra}{disk_str}"
            )
            suggestions.append(_suggestion_for(atype, active))
        elif restored:
            summaries.append(f"{atype}：{len(restored)}条已恢复")
            suggestions.append(f"建议定期巡检 {atype}，确认无新告警")

    if summaries:
        summary = "；".join(summaries)
        suggestion = "\n".join(suggestions)
        status = "error"; hi = True
    else:
        summary = "OPS日志分析完成，未发现任何告警，系统运行正常"
        suggestion = ""; status = "health"; hi = False

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
            "by_type": {
                t: {
                    "active":    len(b["active"]),
                    "restored":  len(b["restored"]),
                }
                for t, b in by_type.items()
            },
            "alarm_list": active_all[:30] if active_all else restored_all[:10],
            "log_files": [str(f.relative_to(log_path)) for f in log_files[:10]]
        }
    }


def _suggestion_for(atype, active):
    if atype == "HD_ALARM_DISK_SINGLE_LINK":
        disks = sorted({a.get("disk_id","?") for a in active if a.get("disk_id")})
        return (f"检查磁盘物理连接（diskId: {', '.join(disks)}），"
                "确认硬盘是否松动或故障")
    elif atype == "hdNetMasterCtrlFaultAlarmHandle":
        return "检查主控模块状态变化原因，确认网络主控是否故障或重启"
    elif atype == "hdNetProcCardFaultAlarm":
        return "检查网卡/业务卡状态，确认是否需要更换或重启相关进程"
    elif atype == "hdNetProcCtrlFaultAlarm":
        return "查看Ctrl进程告警详情，确认故障原因并及时处理"
    elif atype == "hdNetProcSlowLinkAlarm":
        return "检查链路质量，确认是否存在慢链路或网络抖动"
    return "及时处理告警"


# ── CLI ──────────────────────────────────────────────────

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
