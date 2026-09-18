import argparse
import ctypes
import datetime
import json
import os
import queue
import re
import sys
import threading
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

import xtuoj_fetch as fetch
import xtuoj_report as report


class QueueWriter:
    def __init__(self, q):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(text)

    def flush(self):
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.running = False
        cfg = fetch.load_config(None, interactive=False)

        root.title("XTUOJ 班级榜单工具")
        root.geometry("780x600")
        root.minsize(700, 520)

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)

        self.user_var = tk.StringVar(value=cfg.get("user_id", ""))
        self.pwd_var = tk.StringVar(value=cfg.get("password", ""))
        self.group_var = tk.StringVar(value="，".join(cfg.get("groups", [])))
        self.date_var = tk.StringVar(value=datetime.date.today().strftime("%Y-%m-%d"))
        self.win_var = tk.StringVar(value=str(cfg["anomaly"]["window_minutes"]))
        self.cnt_var = tk.StringVar(value=str(cfg["anomaly"]["min_count"]))

        ttk.Label(frame, text="学号/用户名").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.user_var).grid(row=0, column=1, columnspan=3, sticky="ew", pady=3)

        ttk.Label(frame, text="密码").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.pwd_var, show="*").grid(row=1, column=1, columnspan=3, sticky="ew", pady=3)

        ttk.Label(frame, text="班级/小组").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.group_var).grid(row=2, column=1, columnspan=3, sticky="ew", pady=3)

        ttk.Label(frame, text="日期文件夹").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.date_var, width=16).grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(frame, text="多个班级用逗号分隔").grid(row=3, column=2, columnspan=2, sticky="w", padx=8)

        ttk.Label(frame, text="异常检测").grid(row=4, column=0, sticky="w", pady=3)
        rule = ttk.Frame(frame)
        rule.grid(row=4, column=1, columnspan=3, sticky="w", pady=3)
        ttk.Label(rule, text="同一人").pack(side="left")
        ttk.Entry(rule, textvariable=self.win_var, width=5).pack(side="left", padx=4)
        ttk.Label(rule, text="分钟内 AC ≥").pack(side="left")
        ttk.Entry(rule, textvariable=self.cnt_var, width=5).pack(side="left", padx=4)
        ttk.Label(rule, text="题").pack(side="left")

        btns = ttk.Frame(frame)
        btns.grid(row=5, column=0, columnspan=4, sticky="w", pady=(10, 6))
        self.fetch_btn = ttk.Button(btns, text="抓取并生成榜单", command=lambda: self.start(False))
        self.fetch_btn.pack(side="left")
        self.offline_btn = ttk.Button(btns, text="仅用已有数据出图", command=lambda: self.start(True))
        self.offline_btn.pack(side="left", padx=8)
        ttk.Button(btns, text="打开输出文件夹", command=self.open_folder).pack(side="left")

        ttk.Label(frame, text="运行日志").grid(row=6, column=0, columnspan=4, sticky="w", pady=(6, 2))
        log_frame = ttk.Frame(frame)
        log_frame.grid(row=7, column=0, columnspan=4, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(log_frame, command=self.log.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=bar.set)

        self.append("就绪。首次使用请填写学号、密码、班级，然后点「抓取并生成榜单」。")
        self.root.after(100, self.poll)

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_buttons(self, enabled):
        state = "normal" if enabled else "disabled"
        self.fetch_btn.configure(state=state)
        self.offline_btn.configure(state=state)

    def start(self, offline):
        if self.running:
            return
        user = self.user_var.get().strip()
        pwd = self.pwd_var.get()
        groups = [g.strip() for g in re.split(r"[,，;；]", self.group_var.get()) if g.strip()]
        date = self.date_var.get().strip()
        if not offline and (not user or not pwd):
            messagebox.showerror("提示", "请填写学号和密码")
            return
        if not groups:
            messagebox.showerror("提示", "请填写班级/小组名称")
            return
        try:
            win = int(self.win_var.get())
            cnt = int(self.cnt_var.get())
        except ValueError:
            messagebox.showerror("提示", "异常检测的分钟数和题数必须是整数")
            return
        self.running = True
        self.set_buttons(False)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        threading.Thread(target=self.worker,
                         args=(user, pwd, groups, date, win, cnt, offline),
                         daemon=True).start()

    def worker(self, user, pwd, groups, date, win, cnt, offline):
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = QueueWriter(self.queue)
        try:
            cfg = fetch.load_config(None, interactive=False)
            cfg["user_id"] = user
            cfg["password"] = pwd
            cfg["groups"] = groups
            cfg["anomaly"]["window_minutes"] = win
            cfg["anomaly"]["min_count"] = cnt
            (fetch.ROOT / "config.json").write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            args = argparse.Namespace(date=date, group=groups, limit=None,
                                      offline=offline, no_raw=False, scale=None)
            folder = fetch.date_folder(args)
            if offline:
                print("离线模式：使用已有数据出图")
            else:
                print("正在登录并抓取数据，请稍候（人数多时约需半分钟）...")
                fetch.fetch_all(folder, cfg, save_raw=True)
            report.run(folder, cfg, args)
            self.queue.put(("__done__", folder))
        except SystemExit as e:
            self.queue.put(("__error__", str(e)))
        except Exception:
            self.queue.put(("__error__", traceback.format_exc()))
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    def poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if isinstance(item, tuple):
                    kind, payload = item
                    self.running = False
                    self.set_buttons(True)
                    if kind == "__done__":
                        self.append(f"\n全部完成！输出目录：{payload}\n")
                        messagebox.showinfo("完成", f"榜单已生成：\n{payload}")
                    else:
                        self.append(f"\n[错误] {payload}\n")
                        messagebox.showerror("出错了", payload.strip().splitlines()[-1] if payload.strip() else "未知错误")
                else:
                    self.append(item)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def open_folder(self):
        folder = fetch.ROOT / self.date_var.get().strip()
        if folder.exists():
            os.startfile(folder)
        else:
            messagebox.showinfo("提示", f"文件夹还不存在：\n{folder}")


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
