import argparse
import csv
import datetime
import getpass
import hashlib
import html as H
import http.cookiejar
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://oj.xtu.edu.cn"
if getattr(sys, "frozen", False):
    ROOT = pathlib.Path(sys.executable).resolve().parent
else:
    ROOT = pathlib.Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')

DEFAULT_CONFIG = {
    "user_id": "",
    "password": "",
    "groups": ["示例班级"],
    "request_interval": 0.4,
    "render_scale": 2,
    "contests_enabled": True,
    "contest_keyword": "C语言作业",
    "contest_ids": [],
    "anomaly": {"window_minutes": 3, "min_count": 2, "since": ""},
}


def safe_name(name):
    return BAD_NAME_CHARS.sub("_", name).strip() or "未命名"


def deep_merge(base, override):
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(args=None, interactive=True):
    path = ROOT / "config.json"
    example = ROOT / "config.example.json"
    cfg = dict(DEFAULT_CONFIG)
    if example.exists():
        cfg = deep_merge(cfg, json.loads(example.read_text(encoding="utf-8")))
    if path.exists():
        cfg = deep_merge(cfg, json.loads(path.read_text(encoding="utf-8")))
    if args is not None and getattr(args, "group", None):
        cfg["groups"] = list(args.group)
    if args is not None and getattr(args, "scale", None):
        cfg["render_scale"] = args.scale
    if args is not None and getattr(args, "contest_keyword", None):
        cfg["contest_keyword"] = args.contest_keyword
    if args is not None and getattr(args, "no_contests", False):
        cfg["contests_enabled"] = False
    if interactive:
        changed = False
        if not cfg["user_id"]:
            cfg["user_id"] = input("XTUOJ 学号/用户名: ").strip()
            changed = True
        if not cfg["password"]:
            cfg["password"] = getpass.getpass("XTUOJ 密码(输入不回显): ")
            changed = True
        if changed:
            path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"配置已保存到 {path.name}（已加入 .gitignore，请勿外传）")
    return cfg


class Session:
    def __init__(self, interval=0.4):
        self.interval = max(0.0, float(interval))
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [
            ("User-Agent", UA),
            ("Accept-Language", "zh-CN,zh;q=0.9"),
        ]
        self.last = 0.0

    def _wait(self):
        gap = time.time() - self.last
        if gap < self.interval:
            time.sleep(self.interval - gap)
        self.last = time.time()

    def get(self, path, params=None):
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        self._wait()
        with self.opener.open(url, timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")

    def post(self, path, data):
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=body)
        self._wait()
        with self.opener.open(req, timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")


def is_login_page(raw):
    return "<title>Login - XTUOJ</title>" in raw or "UserName or Password Wrong" in raw


def parse_csrf(raw):
    m = re.search(r'name="csrf"\s+value="([^"]+)"', raw)
    return m.group(1) if m else ""


def login(sess, user_id, password):
    sess.get("/loginpage.php")
    csrf = parse_csrf(sess.get("/csrf.php"))
    digest = hashlib.md5(password.encode("utf-8")).hexdigest()
    page = sess.post("/login.php", {
        "user_id": user_id,
        "password": digest,
        "submit": "Login",
        "csrf": csrf,
    })
    if "UserName or Password Wrong" in page:
        raise SystemExit("登录失败：账号或密码错误，请检查 config.json 中的 user_id / password")
    if "logout.php" not in page and "window.location" not in page:
        raise SystemExit("登录失败：服务器返回了未知页面，请稍后重试")
    home = sess.get("/index.php")
    if "logout.php" not in home:
        raise SystemExit("登录失败：未能确认登录状态，请检查账号是否可用")
    return csrf


def cell_text(td):
    t = re.sub(r"<[^>]+>", "", td)
    return H.unescape(t).replace("\t", "").replace("\n", " ").strip()


def tbody_rows(raw):
    m = re.search(r"<tbody[^>]*>(.*?)</tbody>", raw, re.S)
    if not m:
        return []
    return re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S)


def parse_ranklist(raw):
    rows = []
    for tr in tbody_rows(raw):
        tds = [cell_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) < 6 or not tds[0].isdigit() or not tds[1].isdigit():
            continue
        rows.append({
            "rank": tds[0],
            "user": tds[1],
            "name": tds[2],
            "cls": tds[3],
            "ac": tds[4],
            "submit": tds[5],
            "ratio": tds[6] if len(tds) > 6 else "",
        })
    return rows


def parse_status(raw):
    rows = []
    for tr in tbody_rows(raw):
        tds = [cell_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) < 5:
            continue
        text = " ".join(tds)
        mt = TIME_RE.search(text)
        if not mt:
            continue
        user = ""
        mu = re.search(r"userinfo\.php\?user=([^\"'&\s]+)", tr)
        if mu:
            user = H.unescape(mu.group(1)).strip()
        if not user:
            mu = re.search(r"user_id=([0-9A-Za-z_]+)", tr)
            if mu:
                user = mu.group(1)
        if not user:
            for cell in tds[1:3]:
                if re.fullmatch(r"[0-9A-Za-z_]{4,20}", cell):
                    user = cell
                    break
        pid, title = "", ""
        link = re.search(r"problem\.php\?id=(\d+)", tr)
        if link:
            pid = link.group(1)
            cand = re.search(r">\s*(\d{3,5})\s*(?:[-–—]\s*([^<]+?))?\s*<", tr)
            title = (cand.group(2) or "").strip() if cand else ""
        elif re.search(r"problem\.php\?cid=\d+&pid=\d+", tr) and len(tds) > 3:
            pid = tds[3]
        else:
            for cell in tds[1:-1]:
                mm = re.match(r"^(\d{3,5})\s*(?:[-–—]\s*(.+))?$", cell)
                if mm:
                    pid = mm.group(1)
                    title = (mm.group(2) or "").strip()
                    break
        rows.append({
            "run_id": tds[0] if tds[0].isdigit() else "",
            "user": user,
            "problem": pid,
            "title": title,
            "time": f"{mt.group(1)} {mt.group(2)}",
        })
    return rows


def parse_contests(raw):
    contests = []
    m = re.search(r"<tbody[^>]*>(.*?)</tbody>", raw, re.S)
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S) if m else []
    for tr in trs:
        mc = re.search(r"contest\.php\?cid=(\d+)", tr)
        if not mc:
            continue
        tds = [cell_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        title = tds[1] if len(tds) > 1 else ""
        contests.append({"cid": mc.group(1), "title": title})
    return contests


def parse_contest_problems(raw):
    mapping = {}
    for tr in tbody_rows(raw):
        tds = [cell_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) >= 3 and re.fullmatch(r"[A-Z]", tds[1]):
            mapping[tds[1]] = tds[2]
    return mapping


def fetch_ranklist(sess, group, rawdir, csrf):
    rows, start = [], 0
    while True:
        raw = sess.get("/ranklist.php", {
            "prefix": "", "group_name": group, "start": start, "csrf": csrf})
        if is_login_page(raw):
            raise SystemExit("会话失效：抓取榜单时被重定向到登录页，请重新运行")
        if rawdir is not None:
            (rawdir / f"ranklist_{safe_name(group)}_{start}.html").write_text(raw, encoding="utf-8")
        page = parse_ranklist(raw)
        if not page:
            break
        rows.extend(page)
        if len(page) < 50:
            break
        start += 50
    return rows


def fetch_submissions(sess, group, rawdir, cid=None, tag=""):
    params = {"group_name": group, "jresult": 4}
    if cid:
        params["cid"] = cid
    subs, seen, page = [], set(), 0
    while page < 60:
        raw = sess.get("/status.php", params)
        if is_login_page(raw):
            raise SystemExit("会话失效：抓取提交记录时被重定向到登录页，请重新运行")
        if rawdir is not None:
            prefix = f"c{cid}_" if cid else ""
            (rawdir / f"status_{prefix}{safe_name(group)}_{page}.html").write_text(
                raw, encoding="utf-8")
        rows = parse_status(raw)
        new = [r for r in rows if r["run_id"] not in seen]
        if not new:
            break
        for r in rows:
            seen.add(r["run_id"])
        for r in new:
            r["tag"] = tag
        subs.extend(new)
        if len(rows) < 50:
            break
        ids = [int(r["run_id"]) for r in rows if r["run_id"].isdigit()]
        if not ids:
            break
        params["top"] = min(ids) - 1
        page += 1
    return subs


def select_contests(sess, cfg, rawdir):
    explicit = cfg.get("contest_ids") or []
    if explicit:
        return [{"cid": str(c), "title": ""} for c in explicit]
    if not cfg.get("contests_enabled", True):
        return []
    raw = sess.get("/contest.php")
    if rawdir is not None:
        (rawdir / "contests.html").write_text(raw, encoding="utf-8")
    contests = parse_contests(raw)
    keyword = cfg.get("contest_keyword", "")
    if keyword:
        contests = [c for c in contests if keyword in c["title"]]
    return contests


def save_ranklist_csv(folder, rows):
    path = folder / "ranklist.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["名次", "学号", "姓名", "班级", "解决AC", "提交", "比率"])
        for r in rows:
            w.writerow([r["rank"], r["user"], r["name"], r["cls"],
                        r["ac"], r["submit"], r["ratio"]])
    return path


def save_submissions_csv(folder, subs):
    path = folder / "submissions.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["学号", "姓名", "题目", "标题", "AC时间", "RunID", "来源"])
        for s in subs:
            w.writerow([s["user"], s["name"], s["problem"], s["title"],
                        s["time"], s["run_id"], s.get("tag", "")])
    return path


def save_meta(folder, groups, rows, subs, source, fetched_at=None):
    meta = {
        "fetched_at": fetched_at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "groups": groups,
        "source": source,
        "users": len(rows),
        "submissions": len(subs),
    }
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def date_folder(args):
    if getattr(args, "date", None):
        name = args.date
    else:
        name = datetime.date.today().strftime("%Y-%m-%d")
    folder = pathlib.Path(name)
    if not folder.is_absolute():
        folder = ROOT / folder
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def import_html(files):
    rows, mtimes = [], []
    for f in files:
        path = pathlib.Path(f)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise SystemExit(f"文件不存在: {path}")
        mtimes.append(path.stat().st_mtime)
        raw = path.read_text(encoding="utf-8", errors="replace")
        rows.extend(parse_ranklist(raw))
    seen, merged = set(), []
    for r in rows:
        if r["user"] in seen:
            continue
        seen.add(r["user"])
        merged.append(r)
    return merged, (max(mtimes) if mtimes else None)


def fetch_all(folder, cfg, limit=None, save_raw=True):
    user_id = cfg["user_id"]
    password = cfg["password"]
    groups = cfg["groups"]
    if not user_id or not password:
        raise SystemExit("缺少账号密码，请先在 config.json 中填写")
    rawdir = folder / "raw" if save_raw else None
    if rawdir is not None:
        rawdir.mkdir(parents=True, exist_ok=True)
    sess = Session(cfg.get("request_interval", 0.4))
    print(f"登录 {user_id} ...")
    csrf = login(sess, user_id, password)
    rows, seen = [], set()
    for g in groups:
        print(f"抓取榜单: {g}")
        for r in fetch_ranklist(sess, g, rawdir, csrf):
            if r["user"] not in seen:
                seen.add(r["user"])
                rows.append(r)
    if limit:
        rows = rows[:limit]
    known = {r["user"]: r["name"] for r in rows}
    print(f"榜单共 {len(rows)} 人")

    subs = []
    contests = select_contests(sess, cfg, rawdir)
    for g in groups:
        print(f"抓取练习区 AC 提交: {g}")
        subs.extend(fetch_submissions(sess, g, rawdir, tag="练习"))
        for i, c in enumerate(contests, 1):
            title = c["title"] or f"竞赛{c['cid']}"
            print(f"  [{i}/{len(contests)}] 抓取竞赛 {c['cid']} {title}")
            raw = sess.get("/contest.php", {"cid": c["cid"]})
            if rawdir is not None:
                (rawdir / f"contest_{c['cid']}.html").write_text(raw, encoding="utf-8")
            problems = parse_contest_problems(raw)
            got = fetch_submissions(sess, g, rawdir, cid=c["cid"], tag=title)
            for s in got:
                s["title"] = problems.get(s["problem"], s.get("title", ""))
            subs.extend(got)

    seen_runs, merged = set(), []
    for s in subs:
        if not s["run_id"] or s["run_id"] in seen_runs:
            continue
        if limit and s["user"] not in known:
            continue
        seen_runs.add(s["run_id"])
        s["name"] = known.get(s["user"], s.get("name", ""))
        merged.append(s)
    merged.sort(key=lambda s: (s["user"], s["time"], s["run_id"]))
    subs = merged

    save_ranklist_csv(folder, rows)
    save_submissions_csv(folder, subs)
    meta = save_meta(folder, groups, rows, subs, "scrape")
    print(f"共 {len(subs)} 条 AC 提交记录，已保存 ranklist.csv / submissions.csv / meta.json 到 {folder}")
    return rows, subs, meta


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="XTUOJ 榜单抓取（登录+榜单+AC提交记录）")
    ap.add_argument("--date", help="日期文件夹，默认今天，如 2026-09-08")
    ap.add_argument("--group", action="append", help="班级/小组名，可多次传入，覆盖 config.json")
    ap.add_argument("--limit", type=int, help="只抓取榜单前 N 人（调试用）")
    ap.add_argument("--from-html", action="append", default=[],
                    help="导入浏览器保存的榜单 HTML（可多次传入），不登录")
    ap.add_argument("--no-raw", action="store_true", help="不保存原始 HTML 存档")
    ap.add_argument("--no-contests", action="store_true", help="不抓取竞赛/作业的提交记录")
    ap.add_argument("--contest-keyword", help="按标题关键词筛选竞赛，默认取 config.json")
    args = ap.parse_args()

    folder = date_folder(args)
    if args.from_html:
        rows, mtime = import_html(args.from_html)
        if not rows:
            raise SystemExit("未能从 HTML 中解析出榜单数据")
        fetched = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S") if mtime else None
        save_ranklist_csv(folder, rows)
        save_meta(folder, [rows[0]["cls"]] if rows else [], rows, [], "html", fetched_at=fetched)
        print(f"已导入 {len(rows)} 人 -> {folder / 'ranklist.csv'}")
        return
    cfg = load_config(args)
    fetch_all(folder, cfg, limit=args.limit, save_raw=not args.no_raw)


if __name__ == "__main__":
    main()
