#!/usr/bin/env python3
"""
定时论文推送机器人：
- 每次执行时拉取最近 2.5 天的 WAM 论文
- 去重后按相关性排序
- 通过 feishu_sender.py 里的配置发到飞书
- 默认每 48 小时执行一次，可通过环境变量改周期

示例：
  python timed_paper_bot.py --once
  python timed_paper_bot.py
  RUN_INTERVAL_MINUTES=120 python timed_paper_bot.py
  RUN_AT=10:07 python timed_paper_bot.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

from feishu_sender import _post, paper_card, send_card
from paper_bot import fetch_papers, load_seen, log, rank, save_seen


def run_once() -> int:
    backfill = float(os.environ.get("BACKFILL_DAYS", "0"))
    since = datetime.utcnow() - timedelta(days=(backfill or 2.5))
    log(f"===== 定时任务启动 | 时间窗起点 {since.isoformat()} | backfill={backfill} =====")

    papers = fetch_papers(since)
    log(f"召回 {len(papers)} 篇")

    seen = load_seen()
    new = [p for p in papers if p.get("arxiv_id", "") and p["arxiv_id"] not in seen]
    log(f"去重后 {len(new)} 篇")

    ranked = rank(new)
    if not ranked:
        _post(
            send_card(
                "📭 本轮无新增 WAM 论文",
                f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                "**说明**: 时间窗内无符合条件的新论文（不代表没有新工作）。",
            )
        )
        log("本轮无新增论文，已发送空结果通知")
    else:
        for i, p in enumerate(ranked, 1):
            _post(paper_card(p, i, len(ranked)))
            time.sleep(0.35)
        log(f"已发送 {len(ranked)} 篇论文")

    for p in papers:
        aid = p.get("arxiv_id", "")
        if aid:
            seen.add(aid)
    save_seen(seen)
    log("去重状态已更新")
    return len(ranked)


def next_wait_seconds() -> float:
    run_at = os.environ.get("RUN_AT", "").strip()
    if run_at:
        try:
            hour, minute = [int(x) for x in run_at.split(":", 1)]
        except ValueError:
            raise ValueError("RUN_AT 必须为 HH:MM 格式，例如 10:07")

        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    interval_minutes = int(os.environ.get("RUN_INTERVAL_MINUTES", "2880"))
    return max(60, interval_minutes * 60)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
        return

    log("=== WAM 定时推送机器人启动 ===")
    log(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"默认执行间隔: {os.environ.get('RUN_INTERVAL_MINUTES', '2880')} 分钟")

    while True:
        run_once()
        wait = next_wait_seconds()
        log(f"下一次执行时间: {(datetime.now() + timedelta(seconds=wait)).strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("用户中断，退出定时任务")
    except Exception as exc:  # pragma: no cover
        log(f"定时任务异常: {exc}")
        import traceback
        log(traceback.format_exc())
        raise
