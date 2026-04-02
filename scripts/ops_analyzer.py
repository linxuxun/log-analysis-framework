#!/usr/bin/env python3
"""
OPS模块 - 系统运维日志分析

支持解析HD_ALARM_DISK_SINGLE_LINK格式日志:
  [时间][级别][HD][组件:行号] Hd proc alarm id:N diskId:N sn:XXX type:HD_ALARM_DISK_SINGLE_LINK isRestore:0/1 success.

状态判断:
  - error:  any HD_ALARM_* 且 isRestore:0
  - warn:   any HD_ALARM_* 且 isRestore:1
  - health: 无HD_ALARM_*记录
"""
import sys, os, json, argparse, logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OPS] %(levelname)s %(message)s",
    stream=sys.stderr)
logger = logging.getLogger("ops_analyzer")


def parse_alarm(line):
    """
    解析一条 HD_ALARM_* 日志行。
    字段顺序（从左到右）：
      Hd proc alarm id:N diskId:N sn:XXX type:TYPE isRestore:N success.
    先找到 isRestore: 的位置作为锚点，再向前依次找 type/diskId/sn。
    """
    if "HD_ALARM" not in line:
        return None

    result = {"raw": line[:120].strip()}

    # ── 1. isRestore（锚点）──────────────────────────────
    p_restore = line.find("isRestore:")
    if p_restore < 0:
        return None
    # isRestore 后的数字字符
    val_char = line[p_restore + 10:p_restore + 11]
    result["is_restore"] = int(val_char) if val_char.isdigit() else 0

    # ── 2. alarm_type（isRestore 之前最近的值）────────────
    #     "... type:HD_ALARM_DISK_SINGLE_LINK isRestore:0"
    #     找 isRestore: 前一个空格 → type值的右边界
    #     再找该空格前一个空格 → type值的左边界
    space_before_restore = line.rfind(" ", 0, p_restore)        # "LINK"后的空格
    if space_before_restore < 0:
        return None
    space_before_type = line.rfind(" ", 0, space_before_restore)  # "type:"前的空格
    if space_before_type < 0:
        return None
    # type值 = type: 后面那个空格之后，到 space_before_restore 之前
    type_value_start = space_before_type + len("type:")
    result["alarm_type"] = line[type_value_start:space_before_restore].strip()
    if result["alarm_type"] == "":
        result["alarm_type"] = "UNKNOWN"

    # ── 3. diskId（在 type: 之前找最近的）────────────────
    p_disk = line.find("diskId:")
    if 0 <= p_disk < space_before_type:          # 确保在本行type之前
        end_disk = line.find(" ", p_disk + 8)
        if end_disk < 0 or end_disk > space_before_type:
            end_disk = space_before_type
        result["disk_id"] = line[p_disk + 8:end_disk].strip()
    else:
        result["disk_id"] = ""

    # ── 4. sn（在 diskId 之前找）────────────────────────
    p_sn = line.find("sn:")
    if 0 <= p_sn < p_disk:                        # sn 在 diskId 之前
        end_sn = line.find(" ", p_sn + 3)
        if end_sn < 0 or end_sn > space_before_type:
            end_sn = space_before_type
        result["sn"] = line[p_sn + 3:end_sn].strip()
    else:
        result["sn"] = ""

    return result


def analyze_ops(log_path):
    """分析OPS目录，返回符合Schema的字典"""
    alarm_list = []
    total = err = warn = 0

    log_files = list(Path(log_path).rglob("*.log"))
    logger.info("发现日志文件 %d 个", len(log_files))

    for lf in log_files[:20]:
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

    active   = [a for a in alarm_list if a.get("is_restore") == 0]
    restored = [a for a in alarm_list if a.get("is_restore") == 1]

    if active:
        disks = sorted({
            a.get("disk_id", "?")
            for a in active
            if a.get("disk_id") and a.get("disk_id") != "?"
        })
        summary = (
            "发现 %d 条磁盘单链路告警未恢复，"
            "涉及 %d 块磁盘（diskId: %s），需要人工介入处理"
        ) % (len(active), len(disks), ", ".join(disks) if disks else "未知")
        suggestion = (
            "1. 检查告警磁盘物理连接是否松动\n"
            "2. 联系运维确认是否需更换硬盘\n"
            "3. 如已处理完毕，告警将在系统恢复后自动消除"
        )
        status = "error"; hi = True
    elif restored:
        summary = "发现 %d 条告警记录，均已恢复，无需紧急处理" % len(restored)
        suggestion = "建议定期巡检，确认无新的active告警"
        status = "warn"; hi = True
    else:
        summary = "OPS日志分析完成，未发现磁盘告警，系统运行正常"
        suggestion = ""; status = "health"; hi = False

    logger.info("分析完成: total=%d active=%d restored=%d", total, len(active), len(restored))

    return {
        "status": status,
        "human_intervention": hi,
        "summary": summary,
        "suggestion": suggestion,
        "details": {
            "files_scanned": len(log_files),
            "total_lines": total,
            "active_alarms": len(active),
            "restored_alarms": len(restored),
            "alarm_list": active[:20] if active else restored[:5],
            "log_files": [str(f.relative_to(log_path)) for f in log_files[:10]]
        }
    }


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
            "status": "error",
            "human_intervention": True,
            "summary": "日志目录不存在: %s" % args.log_path,
            "suggestion": "请检查日志路径是否正确"
        }, ensure_ascii=False))
        sys.exit(1)

    result = analyze_ops(args.log_path)
    out = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        logger.info("结果已写入: %s", args.output)
    else:
        print(out)
