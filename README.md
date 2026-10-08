# WAM 论文推送机器人

## 文件说明

- `paper_bot.py` - 主脚本
- `requirements.txt` - Python 依赖
- `.github/workflows/bot.yml` - GitHub Actions 定时任务配置

## 部署方式(二选一)

### 方式A: GitHub Actions(推荐,无需本地常开)

1. 创建一个 **Private** GitHub 仓库,把 `paper_bot.py`、`requirements.txt`、`.github/` 目录上传上去
2. 仓库 Settings → Secrets and variables → Actions → New repository secret:
   - `FEISHU_WEBHOOK`: 你的飞书群机器人 Webhook URL
   - `FEISHU_SECRET`: 飞书机器人签名密钥
3. Actions 标签页 → 点 "WAM Paper Bot" → Run workflow → 手动跑一次验证
4. 验证通过后,脚本会自动按 cron 表达式每两天运行一次

**优势**: GitHub 服务器在国外,直连 arXiv 无压力;不用自己养机器;免费。

### 方式B: 本地运行

1. 安装 Python 3.11+
2. `pip install -r requirements.txt`
3. 配置飞书环境变量:
   - CMD: `set FEISHU_WEBHOOK=https://...` `set FEISHU_SECRET=xxx`
   - PowerShell: `$env:FEISHU_WEBHOOK="https://..."` `$env:FEISHU_SECRET="xxx"`
4. 运行: `python paper_bot.py`

**如果本地报 DNS 错误(无法解析 arxiv.org)**:
- 选项1: 设代理 `set HTTPS_PROXY=http://127.0.0.1:7890` 后再跑
- 选项2: 用 Semantic Scholar 源 `set DATA_SOURCE=s2` 后再跑
- 选项3(推荐): 改用方式A部署到 GitHub Actions

## 定时配置

GitHub Actions 的 cron 表达式在 `.github/workflows/bot.yml` 中:
```
- cron: '7 2 */2 * *'
```
这是 UTC 时间,北京时间 = +8小时 → 每两天早上 10:07 运行。

修改 cron 表达式即可改频率,常见参考:
- 每天早上9点: `0 1 * * *`
- 每两天早上10点: `0 2 */2 * *`
- 每周一早上9点: `0 1 * * 1`

## 调试

手动触发一次(不等待定时):
- GitHub Actions 页面 → Actions → WAM Paper Bot → Run workflow
- 本地: `python paper_bot.py` 或 `BACKFILL_DAYS=7 python paper_bot.py`(补推7天)

查看运行日志:
- GitHub Actions: 点某次运行 → 看日志
- 本地: `cat run.log`
