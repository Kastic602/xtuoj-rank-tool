import argparse
import csv
import datetime
import html as H
import json
import pathlib
import re
import statistics
import subprocess
import sys
import time

import xtuoj_fetch as fetch

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def find_browser():
    for p in EDGE_CANDIDATES:
        if pathlib.Path(p).exists():
            return p
    raise SystemExit("未找到 Edge/Chrome,请检查 EDGE_CANDIDATES 路径")


def read_ranklist(folder):
    path = folder / "ranklist.csv"
    if not path.exists():
        raise SystemExit(f"缺少 {path}，请先运行 python xtuoj_report.py 抓取，或用 --from-html 导入")
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fp:
        for r in csv.DictReader(fp):
            ac = re.sub(r"\D", "", r.get("解决AC", "") or "") or "0"
            rows.append({
                "rank": (r.get("名次", "") or "").strip(),
                "user": (r.get("学号", "") or "").strip(),
                "name": (r.get("姓名", "") or "").strip(),
                "cls": (r.get("班级", "") or "").strip(),
                "ac": int(ac),
                "submit": (r.get("提交", "") or "").strip(),
                "ratio": (r.get("比率", "") or "").strip(),
            })
    return rows


def read_submissions(folder):
    path = folder / "submissions.csv"
    if not path.exists():
        return []
    subs = []
    with open(path, encoding="utf-8-sig", newline="") as fp:
        for r in csv.DictReader(fp):
            subs.append({
                "user": (r.get("学号", "") or "").strip(),
                "name": (r.get("姓名", "") or "").strip(),
                "problem": (r.get("题目", "") or "").strip(),
                "title": (r.get("标题", "") or "").strip(),
                "time": (r.get("AC时间", "") or "").strip(),
                "run_id": (r.get("RunID", "") or "").strip(),
                "tag": (r.get("来源", "") or "").strip(),
            })
    return [s for s in subs if s["time"]]


def find_cutoff(rows):
    acs = [r["ac"] for r in rows]
    n = len(acs)
    if n < 4:
        return n
    med = statistics.median(acs)
    best_gap, best_i = 0, None
    for i in range(n - 1):
        if acs[i] <= med and acs[i] > acs[i + 1]:
            gap = acs[i] - acs[i + 1]
            if gap > best_gap:
                best_gap, best_i = gap, i + 1
    return best_i if best_i else n


def detect_anomalies(subs, window_minutes, min_count, since=""):
    def key(s):
        return (s["tag"], s["problem"] or s["run_id"])

    def label(s):
        if s["title"]:
            return f"{s['problem']}({s['title']})"
        return s["problem"] or "?"

    by_user = {}
    for s in subs:
        if since and s["time"][:10] < since:
            continue
        by_user.setdefault(s["user"], []).append(s)
    findings = []
    for user, items in by_user.items():
        items.sort(key=lambda x: x["time"])
        times = []
        for x in items:
            try:
                times.append(datetime.datetime.strptime(x["time"], TIME_FMT))
            except ValueError:
                times.append(None)
        i = 0
        while i < len(items):
            j = i
            keys = {key(items[i])}
            while (j + 1 < len(items) and times[i] and times[j + 1]
                   and (times[j + 1] - times[i]).total_seconds() <= window_minutes * 60):
                j += 1
                keys.add(key(items[j]))
            if len(keys) >= min_count:
                problems, seen_p = [], set()
                for x in items[i:j + 1]:
                    if key(x) not in seen_p:
                        seen_p.add(key(x))
                        problems.append(label(x))
                findings.append({
                    "user": user,
                    "name": items[i]["name"],
                    "source": items[i]["tag"],
                    "start": items[i]["time"],
                    "end": items[j]["time"],
                    "minutes": round((times[j] - times[i]).total_seconds() / 60, 1) if times[i] and times[j] else 0,
                    "count": len(keys),
                    "problems": problems,
                })
                i = j + 1
            else:
                i += 1
    findings.sort(key=lambda x: (x["user"], x["start"]))
    return findings


def write_anomaly_reports(folder, findings, cfg, n_users, n_subs):
    win = cfg["anomaly"]["window_minutes"]
    cnt = cfg["anomaly"]["min_count"]
    since = cfg["anomaly"].get("since", "")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    csv_path = folder / f"{folder.name}_异常报告.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["学号", "姓名", "起始时间", "结束时间", "用时(分钟)", "AC题数", "题目列表", "来源"])
        for f in findings:
            w.writerow([f["user"], f["name"], f["start"], f["end"],
                        f["minutes"], f["count"], " ".join(f["problems"]), f.get("source", "")])
    lines = [
        "XTUOJ 异常提交检测报告",
        f"生成时间: {now}",
        f"检测规则: 同一人 {win} 分钟内 AC ≥ {cnt} 题"
        + (f"（仅统计 {since} 之后）" if since else ""),
        f"数据范围: {n_users} 人 / {n_subs} 条 AC 记录",
        "说明: 本报告仅作为复核线索，可能是手速快或复制粘贴代码，请结合提交代码人工判断，不作结论。",
        "",
    ]
    if findings:
        lines.append(f"共发现 {len(findings)} 组可疑密集 AC：")
        lines.append("")
        for idx, f in enumerate(findings, 1):
            lines.append(f"{idx}. {f['user']} {f['name']}")
            src = f" [{f['source']}]" if f.get("source") else ""
            lines.append(f"   {f['start']} ~ {f['end']}（{f['minutes']} 分钟）共 {f['count']} 题{src}: "
                         + " ".join(f["problems"]))
    else:
        lines.append("未发现符合规则的密集 AC 记录。")
    txt_path = folder / f"{folder.name}_异常报告.txt"
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, txt_path


def build_html(rows, title, meta, show_cls, hide_name, highlight_top3, accent):
    trs = []
    for i, r in enumerate(rows):
        cls_bg = " class='top'" if highlight_top3 and i < 3 else ""
        cells = f"<td>{r['rank']}</td><td>{r['user']}</td>"
        if not hide_name:
            cells += f"<td>{r['name']}</td>"
        if show_cls:
            cells += f"<td>{r['cls']}</td>"
        cells += f"<td class='num'>{r['ac']}</td><td class='num'>{r['submit']}</td>"
        trs.append(f"<tr{cls_bg}>{cells}</tr>")
    ths = "<th>名次</th><th>学号</th>"
    if not hide_name:
        ths += "<th>姓名</th>"
    if show_cls:
        ths += "<th>班级</th>"
    ths += "<th>解决(AC)</th><th>提交</th>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  body {{ margin: 0; padding: 28px 32px 26px; font-family: "Microsoft YaHei","PingFang SC",sans-serif; color: #222; background: #fff; width: 1080px; }}
  h1 {{ font-size: 24px; margin: 0 0 6px; letter-spacing: 1px; }}
  .meta {{ color: #888; font-size: 13px; margin: 0 0 14px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 15px; }}
  th, td {{ border: 1px solid #c9d4e4; padding: 9px 12px; text-align: center; }}
  thead th {{ background: {accent}; color: #fff; font-weight: 600; white-space: nowrap; }}
  tbody tr:nth-child(even) td {{ background: #f2f6fc; }}
  td {{ font-family: Consolas,"Microsoft YaHei",monospace; }}
  tr.top td {{ background: #fdf3d8 !important; font-weight: 600; }}
  .num {{ font-family: Consolas,monospace; }}
</style></head>
<body>
<h1>{H.escape(title)}</h1>
<p class="meta">{H.escape(meta)}</p>
<table><thead><tr>{ths}</tr></thead><tbody>{''.join(trs)}</tbody></table>
</body></html>"""


def estimate_height(n_rows):
    return int(28 + 40 + 26 + 40 + n_rows * 44 + 26 + 20 + 12)


def render(html_text, folder, stem, height, scale):
    html_path = folder / f"{stem}.html"
    html_path.write_text(html_text, encoding="utf-8")
    png = folder / f"{stem}.png"
    if png.exists():
        png.unlink()
    cmd = [find_browser(), "--headless", "--disable-gpu", "--hide-scrollbars",
           f"--force-device-scale-factor={scale}",
           f"--window-size=1180,{height}",
           f"--screenshot={png}", html_path.as_uri()]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(cmd, check=True, timeout=90, creationflags=flags,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if png.exists() and png.stat().st_size > 5000:
            break
        time.sleep(0.2)
    return png


def load_meta(folder):
    path = folder / "meta.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def run(folder, cfg, args):
    rows = read_ranklist(folder)
    if not rows:
        raise SystemExit(f"{folder} 中 ranklist.csv 没有数据")
    subs = read_submissions(folder)
    meta = load_meta(folder)

    classes = []
    for r in rows:
        if r["cls"] and r["cls"] not in classes:
            classes.append(r["cls"])
    show_cls = len(classes) > 1
    cls_label = classes[0] if len(classes) == 1 else ("多班级" if classes else "未知班级")
    base = f"{folder.name}_{cls_label.replace(' ', '')}"

    cutoff = find_cutoff(rows)
    fast, slow = rows[:cutoff], rows[cutoff:]
    fetched = meta.get("fetched_at", "")
    meta_line = (f"数据采集时间: {fetched} · " if fetched else "") + f"共 {len(rows)} 人"

    scale = args.scale or cfg.get("render_scale", 2)
    pngs = []

    fast_html = build_html(fast, f"{cls_label} XTUOJ 做题红榜", meta_line,
                           show_cls, hide_name=False, highlight_top3=True, accent="#c0392b")
    pngs.append(render(fast_html, folder, f"{base}_红榜",
                       estimate_height(len(fast)), scale))

    if slow:
        slow_html = build_html(slow, f"{cls_label} XTUOJ 做题黑榜", meta_line,
                               show_cls, hide_name=True, highlight_top3=False, accent="#2f2f2f")
        pngs.append(render(slow_html, folder, f"{base}_黑榜",
                           estimate_height(len(slow)), scale))
    else:
        print("未发现明显断崖，全班进度接近，不生成黑榜")

    for p in pngs:
        print(f"已生成图片: {p.name} ({p.stat().st_size // 1024} KB)")

    findings = []
    if subs:
        win = cfg["anomaly"]["window_minutes"]
        cnt = cfg["anomaly"]["min_count"]
        findings = detect_anomalies(subs, win, cnt, cfg["anomaly"].get("since", ""))
        csv_path, txt_path = write_anomaly_reports(folder, findings, cfg, len(rows), len(subs))
        print(f"异常检测: {len(findings)} 组可疑记录 -> {csv_path.name} / {txt_path.name}")
    else:
        print("无 AC 提交数据，跳过异常检测（可用 xtuoj_fetch.py 抓取后重试）")
    return pngs, findings


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="XTUOJ 榜单出图 + 异常检测（总入口）")
    ap.add_argument("--date", help="日期文件夹，默认今天，如 2026-09-08")
    ap.add_argument("--group", action="append", help="班级/小组名，可多次传入，覆盖 config.json")
    ap.add_argument("--limit", type=int, help="只抓取榜单前 N 人（调试用）")
    ap.add_argument("--offline", action="store_true", help="不抓取，使用已有 CSV 出图")
    ap.add_argument("--no-raw", action="store_true", help="不保存原始 HTML 存档")
    ap.add_argument("--scale", type=int, help="截图清晰度倍数，默认取 config.json（2）")
    args = ap.parse_args()

    folder = fetch.date_folder(args)
    if args.offline:
        cfg = fetch.load_config(args, interactive=False)
    else:
        cfg = fetch.load_config(args)
        fetch.fetch_all(folder, cfg, limit=args.limit, save_raw=not args.no_raw)
    run(folder, cfg, args)


if __name__ == "__main__":
    main()
