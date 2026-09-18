# XTUOJ 班级榜单工具

自动登录 XTUOJ，抓取指定班级/小组的榜单与作业提交记录，生成**红榜 / 黑榜**两张图片，并检测短时间内 AC 多道不同题目的异常提交。

- 自动登录、自动翻页，无需手工保存网页
- 纯 Python 标准库，零第三方依赖
- 支持打包成单文件 EXE，双击即用

## 使用

### 方式一：Python

```powershell
python xtuoj_report.py
```

首次运行会生成 `config.json` 并提示输入账号密码，之后按提示操作即可。

### 方式二：打包 EXE

```powershell
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name "XTUOJ榜单工具" xtuoj_gui.py
```

双击生成的 `XTUOJ榜单工具.exe`，在界面填写学号、密码、班级后点「抓取并生成榜单」。

> 出图依赖本机 Edge 或 Chrome（Windows 一般自带 Edge）。

## 配置（config.json）

```json
{
  "user_id": "你的学号",
  "password": "你的密码",
  "groups": ["示例班级"],
  "request_interval": 0.4,
  "render_scale": 2,
  "contests_enabled": true,
  "contest_keyword": "C语言作业",
  "contest_ids": [],
  "anomaly": { "window_minutes": 3, "min_count": 2, "since": "" }
}
```

| 字段 | 说明 |
|---|---|
| `groups` | 班级/小组名，可写多个（自动显示"班级"列） |
| `contests_enabled` | 是否抓取竞赛/作业的提交记录，`false` 则只抓练习区 |
| `contest_keyword` | 按标题关键词筛选竞赛；也可用 `contest_ids` 直接指定竞赛号 |
| `anomaly` | 同一人 `window_minutes` 分钟内 AC ≥ `min_count` 道不同题目即记录，`since` 可只统计某日期之后 |
| `render_scale` | 截图清晰度倍数 |

> `config.json` 含明文密码，已被 `.gitignore` 忽略，请勿提交或外传。

## 常用命令

```powershell
python xtuoj_report.py                    # 抓取 + 出图 + 异常报告
python xtuoj_report.py --group "示例班级"  # 临时指定班级（可多次传入）
python xtuoj_report.py --date 2026-01-01  # 指定日期文件夹（默认今天）
python xtuoj_report.py --offline          # 不抓取，用已有数据重新出图
python xtuoj_report.py --no-contests      # 不抓竞赛，只抓练习区
python xtuoj_fetch.py                     # 只抓数据不出图
```

## 输出

每次运行在程序目录下创建 `日期/` 文件夹：

| 文件 | 说明 |
|---|---|
| `ranklist.csv` | 名次 / 学号 / 姓名 / 班级 / 解决AC / 提交 / 比率 |
| `submissions.csv` | 学号 / 姓名 / 题目 / 标题 / AC时间 / RunID / 来源 |
| `{日期}_{班级}_红榜.png` | 快组：显示姓名，前三名高亮 |
| `{日期}_{班级}_黑榜.png` | 慢组：姓名隐去、保留学号 |
| `{日期}_异常报告.csv/.txt` | 短时间内 AC 多道不同题目的线索 |
| `raw/` | 原始网页存档，解析出错时可离线排查 |

## 规则

- **红/黑榜分界**：按名次取 AC 数，在中位数以下找相邻名次的最大断崖处分界；无断崖则只生成红榜。
- **异常检测**：同一人短时间内 AC 多道**不同题目**（默认 3 分钟 / 2 题）即记入报告；同一题重复提交不计。报告仅为复核线索，请结合提交代码人工判断。

## 已知限制

- 竞赛题目在 OJ 上不显示题库编号，报告中以"字母 + 标题"表示。
- OJ 页面改版可能导致解析失败：解析逻辑集中在 `xtuoj_fetch.py` 的 `parse_ranklist` / `parse_status` / `parse_contests`。
- 仅抓取榜单与 AC 提交，不抓取代码内容。
