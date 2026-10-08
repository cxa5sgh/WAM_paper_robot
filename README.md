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
   - `FEISHU_KEYWORD`: 飞书机器人安全设置中配置的关键词
   - `XHS_COOKIE`: 可选，小红书网页 Cookie；不配置则不启用 xhs 搜索
   - `XHS_SIGNER_URL`: 可选，ReaJason/xhs Playwright 签名服务的 `/sign` 地址；需与 Cookie 同一签名环境
4. 如需自定义小红书搜索词,在 Actions Variables 添加 `XHS_SEARCH_KEYWORD` (默认 `世界模型`)
5. Actions 标签页 → 点 "WAM Paper Bot" → Run workflow → 手动跑一次验证
6. 验证通过后,脚本会每周一北京时间 09:00 运行,论文与社媒内容合计最多推送 3 条

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
- cron: '0 1 * * 1'
```
这是 UTC 时间,北京时间 = +8小时 → 每周一早上 09:00 运行,搜索最近一周内容。

内容来源包括 arXiv、Semantic Scholar、Google News RSS 索引中的知乎内容;配置 `XHS_COOKIE` 与 `XHS_SIGNER_URL` 后,额外通过 `xhs` SDK 搜索小红书。该 SDK 依赖有效 Cookie 和外部 Playwright 签名服务,平台可能限流或要求更新 Cookie。社媒来源由最近结果补足,每周总计最多发送 3 条。

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
