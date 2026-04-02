#!/usr/bin/env python3
"""
OPS模块 - 系统运维日志分析

支持解析全部告警类型（按真实日志格式适配）:

磁盘类:
  HD_ALARM_DISK_SINGLE_LINK  type:... isRestore:0/1
  HD_ALARM_DISK_FAULT        type:... isRestore:0/1
  hdProcRDiskAlarm           type:... isRestore:0/1
  hdProcUDiskAlarm           type:... isRestore:0/1

网络链路类:
  hdNetMasterCtrlFaultAlarmHandle  change(N -> M)
  hdNetMasterReportCtrlFaultAlarm  isRestore (O)  ← 括号+字母O
  hdNetProcCardFaultAlarm          fault (N) times / restore 0
  hdNetProcSlowLinkAlarm           subhealth Alarm / restore Alarm
  HD_ALARM_NET_FAULT               isRestore:0/1
  HD_ALARM_NET_LINK_SLOW           restore 0 / restore 1

加密/其他:
  hdProcEncAlarm              isRestore:0/1
  HD_ALARM_SD_FAULT           "failed, ret:" 出现即告警
"""
import sys, os, json, argparse, logging, re
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OPS] %(levelname)s %(message)s",
    stream=sys.stderr)
logger = logging.getLogger("ops_analyzer")

# ── 告警类型注册（顺序影响匹配优先级）───────────────────────

ALARM_TYPES = [
    # 网络主控类（精确匹配放前面）
    "hdNetMasterCtrlFaultAlarmHandle",   # change(N -> M)
    "hdNetMasterReportCtrlFaultAlarm",  # isRestore (O)  ← 特殊格式
    "hdNetProcCardFaultAlarm",           # fault (N) times / restore 0
    "hdNetProcSlowLinkAlarm",            # subhealth Alarm / restore Alarm
    "hdNetProcNetAlarm",                 # HD_ALARM_NET_* restore 0/1
    # 磁盘/存储类
    "HD_ALARM_DISK_SINGLE_LINK",         # isRestore:0/1
    "HD_ALARM_DISK_FAULT",               # isRestore:0/1
    "hdProcRDiskAlarm",                  # isRestore:0/1
    "hdProcUDiskAlarm",                  # isRestore:0/1
    "hdProcEncAlarm",                    # isRestore:0/1
    # 网络故障类
    "HD_ALARM_NET_FAULT",                # isRestore:0/1
    "HD_ALARM_NET_LINK_SLOW",            # restore 0 / restore 1
    # SD卡类
    "hdSdDetectCollect",                 # "failed, ret:" → SD故障
]


def _find_in_line(line, key, before_idx=None):
    """在 line[0:before_idx] 范围内找 key，返回起点索引或 -1。"""
    end = before_idx if before_idx is not None else len(line)
    return line.find(key, 0, end)


def _extract_value_after(line, key, before_idx=None):
    """找 key，在其后截取到空格为止的值（限制在 before_idx 之前）。"""
    p = _find_in_line(line, key, before_idx)
    if p < 0:
        return "", -1
    start = p + len(key)
    end = line.find(" ", start)
    if end < 0 or (before_idx is not None and end > before_idx):
        end = before_idx if before_idx is not None else len(line)
    return line[start:end].strip(), p


def _is_restore_colon(line, before_idx=None):
    """
    通用 isRestore:0 / isRestore:1 提取。
    isRestore 在前，type 在后，两者顺序固定。
    """
    p_isr = _find_in_line(line, "isRestore:", before_idx)
    if p_isr < 0:
        return None, -1
    val = line[p_isr + 10:p_isr + 11]
    return int(val) if val.isdigit() else None, p_isr


def _is_restore_parens(line):
    """
    isRestore (O) 格式提取（括号+字母O）。
    hdNetMasterReportCtrlFaultAlarm 使用此格式。
    'O' = 0（字母O），'1' = 1。
    """
    m = re.search(r"isRestore\s*\(\s*([O01])\s*\)", line)
    if not m:
        return None, -1
    val = m.group(1)
    return 0 if val in ("O", "0") else 1 if val == "1" else None, m.start()


def parse_alarm(line):
    """
    识别并解析一条告警日志，返回 dict 或 None。
    同一行可能包含多个告警关键字，只匹配第一个。
    """
    hit = None
    for atype in ALARM_TYPES:
        if atype in line:
            hit = atype
            break
    if hit is None:
        return None

    result = {"alarm_type": hit, "raw": line[:120].strip()}

    # ══════════════════════════════════════════════
    # A. hdNetMasterCtrlFaultAlarmHandle
    #    "change(0 -> 1)" 或 "change(1 -> 0)"
    # ══════════════════════════════════════════════
    if hit == "hdNetMasterCtrlFaultAlarmHandle":
        m = re.search(r"change\s*\(\s*(\d+)\s*->\s*(\d+)\s*\)", line)
        if m:
            result["from_val"] = int(m.group(1))
            result["to_val"]   = int(m.group(2))
            result["is_restore"] = 0 if result["to_val"] == 1 else 1
        else:
            result["is_restore"] = 0
        return result

    # ══════════════════════════════════════════════
    # B. hdNetMasterReportCtrlFaultAlarm
    #    "isRestore (O)"  ← 括号格式，O=未恢复 1=恢复
    # ══════════════════════════════════════════════
    if hit == "hdNetMasterReportCtrlFaultAlarm":
        val, _ = _is_restore_parens(line)
        result["is_restore"] = val if val is not None else 0
        # 提取 ctrl id 和 net type
        m_ctrl = re.search(r"ctrl\s+id\s+\((\d+)\)", line)
        m_net  = re.search(r"net\s+type\s+\(([^)]+)\)", line)
        if m_ctrl: result["ctrl_id"] = m_ctrl.group(1)
        if m_net:  result["net_type"] = m_net.group(1).strip()
        return result

    # ══════════════════════════════════════════════
    # C. hdNetProcCardFaultAlarm
    #    格式1: "... fault (N) times."      ← 触发N次故障
    #    格式2: "... fault alarm restore 0" ← 未恢复
    #    格式3: "... fault alarm restore 1" ← 已恢复
    # ══════════════════════════════════════════════
    if hit == "hdNetProcCardFaultAlarm":
        m_restore = re.search(r"\bfault\s+alarm\s+restore\s+([01])\b", line)
        if m_restore:
            result["is_restore"] = int(m_restore.group(1))
        else:
            # "fault (13) times" → 触发故障
            result["is_restore"] = 0
        # 提取 iface / ctrl id
        m_iface  = re.search(r"iface\s+\(([^)]+)\)", line)
        m_ctrlid = re.search(r"ctrl\s+id\s+(\d+)", line)
        if m_iface:  result["iface"]   = m_iface.group(1).strip()
        if m_ctrlid: result["ctrl_id"] = m_ctrlid.group(1)
        return result

    # ══════════════════════════════════════════════
    # D. hdNetProcNetAlarm
    #    "alarm type HD_ALARM_NET_LINK_SLOW restore 0"
    #    "alarm type HD_ALARM_NET_FAULT isRestore:0"
    # ══════════════════════════════════════════════
    if hit == "hdNetProcNetAlarm":
        # 提取内嵌的 alarm type（优先级：LINK_SLOW > NET_FAULT）
        if "HD_ALARM_NET_LINK_SLOW" in line:
            result["alarm_type"] = "HD_ALARM_NET_LINK_SLOW"
        elif "HD_ALARM_NET_FAULT" in line:
            result["alarm_type"] = "HD_ALARM_NET_FAULT"
        else:
            result["alarm_type"] = "hdNetProcNetAlarm"
        # isRestore 提取（restore 0 / restore 1 / isRestore:0）
        m_r = re.search(r"restore\s+([01])\b", line)
        if m_r:
            result["is_restore"] = int(m_r.group(1))
        else:
            p_isr = _find_in_line(line, "isRestore:")
            if p_isr >= 0:
                val = line[p_isr + 10:p_isr + 11]
                result["is_restore"] = int(val) if val.isdigit() else 0
            else:
                result["is_restore"] = 0
        return result

    # ══════════════════════════════════════════════
    # E. hdNetProcSlowLinkAlarm
    #    "subhealth Alarm" → 未恢复（warn）
    #    "restore Alarm"   → 已恢复
    #    "restore 0"       → 未恢复
    #    "restore 1"       → 已恢复
    # ══════════════════════════════════════════════
    if hit == "hdNetProcSlowLinkAlarm":
        if "subhealth Alarm" in line or "subhealth" in line and "restore" not in line:
            result["is_restore"] = 0
        elif "restore Alarm" in line or re.search(r"\brestore\s+1\b", line):
            result["is_restore"] = 1
        else:
            result["is_restore"] = 0
        return result

    # ══════════════════════════════════════════════
    # E-L. isRestore: 系列（disk / enc / net）
    #   isRestore 在前，type 在后
    #   type 值右边界就是 isRestore 左边界
    # ══════════════════════════════════════════════
    if hit in (
        "HD_ALARM_DISK_SINGLE_LINK", "HD_ALARM_DISK_FAULT",
        "hdProcRDiskAlarm", "hdProcUDiskAlarm", "hdProcEncAlarm",
        "HD_ALARM_NET_FAULT", "HD_ALARM_NET_LINK_SLOW",
    ):
        p_isr = _find_in_line(line, "isRestore:")
        if p_isr < 0:
            return None
        type_end = p_isr          # type 值右边界
        val = line[p_isr + 10:p_isr + 11]
        result["is_restore"] = int(val) if val.isdigit() else 0

        # alarm_type 本身
        p_type = _find_in_line(line, "type:")
        if 0 <= p_type < p_isr:
            type_val_end = line.find(" ", p_type + 5)
            if type_val_end < 0 or type_val_end > p_isr:
                type_val_end = p_isr
            raw_type = line[p_type + 5:type_val_end].strip()
            result["alarm_type"] = raw_type if raw_type else hit

        # sn
        p_sn = _find_in_line(line, "sn:")
        if 0 <= p_sn < p_isr:
            sn_end = line.find(" ", p_sn + 3)
            if sn_end < 0 or sn_end > p_isr:
                sn_end = p_isr
            result["sn"] = line[p_sn + 3:sn_end].strip()

        # diskId
        p_disk = _find_in_line(line, "diskId:")
        if 0 <= p_disk < p_isr:
            disk_end = line.find(" ", p_disk + 8)
            if disk_end < 0 or disk_end > p_isr:
                disk_end = p_isr
            result["disk_id"] = line[p_disk + 8:disk_end].strip()
        # encId（兼容 DiskId 大写形式）
        p_enc = _find_in_line(line, "encId:")
        if 0 <= p_enc < p_isr:
            enc_end = line.find(" ", p_enc + 6)
            if enc_end < 0 or enc_end > p_isr:
                enc_end = p_isr
            result["enc_id"] = line[p_enc + 6:enc_end].strip()

        return result

    # ══════════════════════════════════════════════
    # M. hdSdDetectCollect（SD卡检测）
    #    "SD get perf stat info failed, ret:-N" → SD卡故障
    # ══════════════════════════════════════════════
    if hit == "hdSdDetectCollect":
        if "failed" in line.lower() and "ret:" in line:
            result["is_restore"] = 0
            result["alarm_type"] = "HD_ALARM_SD_FAULT"
            m = re.search(r"ret:([-\d]+)", line)
            if m:
                result["ret_code"] = m.group(1)
        else:
            result["is_restore"] = 1
            result["alarm_type"] = "HD_ALARM_SD_FAULT"
        return result

    return result


# ── 分析主函数 ──────────────────────────────────────────

def analyze_ops(log_path):
    alarm_list  = []
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

    # ── 按类型分组 ─────────────────────────────────
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
            ids = sorted({
                a.get(x, "?")
                for a in active
                for x in ["disk_id", "enc_id", "ctrl_id", "net_type"]
                if a.get(x) and a.get(x) != "?"
            })
            id_str = f"，涉及 {', '.join(ids)}" if ids else ""
            summaries.append(f"{atype}：{len(active)}条未恢复{id_str}")
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
            "active_alarms":   len(active_all),
            "restored_alarms": len(restored_all),
            "by_type": {t: {"active": len(b["active"]), "restored": len(b["restored"])}
                        for t, b in by_type.items()},
            "alarm_list": active_all[:30] if active_all else restored_all[:10],
            "log_files": [str(f.relative_to(log_path)) for f in log_files[:10]]
        }
    }


def _suggestion_for(atype, active):
    if atype == "hdNetMasterCtrlFaultAlarmHandle":
        slots = [str(a.get("to_val","?")) for a in active]
        return f"检查主控模块状态变化（slots: {', '.join(slots)}），确认是否故障"
    elif atype == "hdNetMasterReportCtrlFaultAlarm":
        ctrls = [a.get("ctrl_id","?") for a in active]
        return f"检查主控Report故障（ctrl_id: {', '.join(ctrls)}），确认网络主控状态"
    elif atype == "hdNetProcCardFaultAlarm":
        ifaces = [a.get("iface","?") for a in active]
        return f"检查网卡状态（iface: {', '.join(ifaces)}），确认是否需更换或重启"
    elif atype == "hdNetProcSlowLinkAlarm":
        return "检查链路质量，确认是否存在慢链路或网络抖动"
    elif atype in ("HD_ALARM_DISK_SINGLE_LINK", "HD_ALARM_DISK_FAULT",
                    "hdProcRDiskAlarm", "hdProcUDiskAlarm"):
        disks = sorted({a.get("disk_id","?") for a in active if a.get("disk_id")})
        sns = [a.get("sn","")[:20] for a in active[:3]]
        return f"检查磁盘（diskId: {', '.join(disks)}，SN: {', '.join(sns)}），确认硬盘是否松动或故障"
    elif atype == "hdProcEncAlarm":
        return "检查加密引擎状态，确认加密模块是否异常"
    elif atype in ("HD_ALARM_NET_FAULT", "HD_ALARM_NET_LINK_SLOW"):
        return "检查网络链路状态，确认故障链路并及时处理"
    elif atype == "HD_ALARM_SD_FAULT":
        codes = [a.get("ret_code","?") for a in active]
        return f"检查SD卡状态（ret: {', '.join(codes)}），确认SD卡是否松动或故障"
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
