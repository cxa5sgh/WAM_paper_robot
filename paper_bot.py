#!/usr/bin/env python3
"""
WAM 论文双日推送机器人 v2 (国内友好版)
==============================
数据源优先级:
  1. arXiv API (直连或走代理)
  2. Semantic Scholar (arXiv 不可用时兜底)

国内使用建议:
  - 方案A(推荐): 部署到 GitHub Actions (服务器在国外，直连 arXiv 无压力)
  - 方案B: 本地运行 + 设置 HTTPS_PROXY 环境变量走代理
  - 方案C: 本地运行 + 设 DATA_SOURCE=s2 只用 Semantic Scholar (有时国内可通)

用法:
  python paper_bot.py                     # 正常双日抓取
  python paper_bot.py --reset-seen        # 清空 seen.json 历史记录
  DATA_SOURCE=s2 python paper_bot.py      # 只用 Semantic Scholar
  BACKFILL_DAYS=7 python paper_bot.py     # 手动补推过去 7 天
"""
from __future__ import annotations

import base64, hashlib, hmac, json, os, re, sys, time
from datetime import datetime, timedelta
from typing import Any

import requests

# ======================== 配置区 ========================

# arXiv 检索参数
ARXIV_CATS = ["cs.RO", "cs.CV", "cs.AI", "cs.LG"]
WAM_TERMS = [
    '"world action model"', '"world-action model"', '"video world model"',
    "embodied world model", "action-video joint model", "world model policy",
    "DreamZero", "Fast-WAM", "Motubrain", "Being-H",
    "world model robotics", "world model manipulation",
]
# 噪声过滤
EXCLUDE_RE = re.compile(r"\b(survey|review|benchmark dataset)\b", re.I)

# 数据源选择: "auto"(默认,先arXiv后S2) / "arxiv" / "s2"
DATA_SOURCE = os.environ.get("DATA_SOURCE", "auto").lower()

# 输出配置
TOP_N       = 3
WINDOW_DAYS = 3.0  # 每次抓取最近 3 天，适合周一/三/五 9:00 定时推送
SEEN_FILE   = "seen.json"
LOG_FILE    = "run.log"

# 飞书
WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
SECRET  = os.environ.get("FEISHU_SECRET", "")

# 代理 (脚本不会主动连代理, 但 requests 库会读这些环境变量)
# Windows CMD:      set HTTPS_PROXY=http://127.0.0.1:7890
# Windows PowerShell: $env:HTTPS_PROXY="http://127.0.0.1:7890"
# macOS/Linux:      export HTTPS_PROXY="http://127.0.0.1:7890"

# arXiv API 端点
ARXIV_ENDPOINTS = [
    "https://export.arxiv.org/api/query",
    "https://arxiv.org/api/query",
]

# Semantic Scholar
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"

# 重试配置
MAX_RETRIES = 3
REQUEST_TIMEOUT = 20


# ======================== 日志 ========================

def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ======================== 状态管理 ========================

def load_seen() -> set[str]:
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(s: set[str]) -> None:
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(s), f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"写 seen.json 失败: {e}")


def reset_seen() -> None:
    save_seen(set())
    print(f"已清空 {SEEN_FILE}")


def aid_of(entry_id: str) -> str:
    """http://arxiv.org/abs/2501.12345v2 -> 2501.12345"""
    return entry_id.rstrip("/").split("/")[-1].split("v")[0]


# ======================== 网络请求 ========================

def _http_get(url: str, params: dict | None = None, timeout: int = REQUEST_TIMEOUT) -> requests.Response | None:
    """带重试的 HTTP GET"""
    headers = {"User-Agent": "WAM-PaperBot/2.0 (research automation)"}
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                wait = 2 ** attempt + 2
                log(f"  限流(429), 等 {wait}s 后重试 ({attempt+1}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            log(f"  HTTP {r.status_code}, 重试 {attempt+1}/{MAX_RETRIES}")
            time.sleep(1)
        except requests.exceptions.ConnectionError as e:
            log(f"  连接失败({attempt+1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt + 1)
        except requests.exceptions.Timeout:
            log(f"  超时({attempt+1}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt + 1)
        except requests.exceptions.RequestException as e:
            log(f"  请求异常({attempt+1}/{MAX_RETRIES}): {e}")
            break
    return None


# ======================== 召回: arXiv ========================

def fetch_from_arxiv(since: datetime) -> list[dict]:
    """从 arXiv API 直接拉数据 (XML 格式)"""
    cats = " OR ".join(f"cat:{c}" for c in ARXIV_CATS)
    terms_parts = []
    for t in WAM_TERMS:
        if t.startswith('"') and t.endswith('"'):
            terms_parts.append(f'all:{t}')
        elif " " in t:
            terms_parts.append(f'all:"{t}"')
        else:
            terms_parts.append(f"all:{t}")
    terms = " OR ".join(terms_parts)
    search_query = f"({cats}) AND ({terms})"
    params = {
        "search_query": search_query,
        "max_results": 120,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    import xml.etree.ElementTree as ET
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for endpoint in ARXIV_ENDPOINTS:
        log(f"  尝试 arXiv: {endpoint}")
        r = _http_get(endpoint, params)
        if r is None:
            log(f"    失败, 试下一个")
            continue
        try:
            root = ET.fromstring(r.text.encode("utf-8") if isinstance(r.text, str) else r.text)
        except ET.ParseError as e:
            log(f"    XML 解析失败: {e}")
            continue

        papers = []
        for entry in root.findall("atom:entry", ns):
            published_elem = entry.find("atom:published", ns)
            if published_elem is None:
                continue
            pub_str = published_elem.text
            try:
                pub_dt = datetime.strptime(pub_str[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue
            if pub_dt < since:
                continue

            title_elem = entry.find("atom:title", ns)
            title = title_elem.text.strip() if title_elem is not None else ""
            if EXCLUDE_RE.search(title):
                continue

            summary_elem = entry.find("atom:summary", ns)
            abstract = summary_elem.text.strip() if summary_elem is not None else ""

            authors = []
            for author in entry.findall("atom:author", ns):
                name_elem = author.find("atom:name", ns)
                if name_elem is not None and name_elem.text:
                    authors.append(name_elem.text.strip())

            id_elem = entry.find("atom:id", ns)
            entry_id = id_elem.text if id_elem is not None else ""
            arxiv_id = aid_of(entry_id) if entry_id else ""

            pdf_url = ""
            for link in entry.findall("atom:link", ns):
                if link.get("type") == "application/pdf":
                    pdf_url = link.get("href", "")
                    break

            papers.append({
                "title": title.replace("\n", " "),
                "authors": ", ".join(authors[:4]),
                "abstract": abstract[:500],
                "url": entry_id,
                "pdf": pdf_url,
                "published": pub_dt.strftime("%Y-%m-%d"),
                "arxiv_id": arxiv_id,
                "source": "arxiv",
            })

        if papers:
            log(f"  arXiv 召回 {len(papers)} 篇")
            return papers
        else:
            log(f"  {endpoint} 返回 0 篇")

    return []


# ======================== 召回: Semantic Scholar ========================

def fetch_from_s2(since: datetime) -> list[dict]:
    """从 Semantic Scholar 召回"""
    # 用更宽泛的搜索词提高召回率
    search_query = " OR ".join(WAM_TERMS[:6])  # 用前6个核心词
    params = {
        "query": search_query,
        "limit": 120,
        "fields": "title,authors,abstract,year,publicationTypes,citationCount,externalIds,url",
    }

    r = _http_get(S2_API, params)
    if r is None:
        log("  Semantic Scholar 连接失败")
        return []

    try:
        data = r.json()
    except Exception as e:
        log(f"  S2 JSON 解析失败: {e}")
        return []

    papers = []
    for paper in data.get("data", []):
        year = paper.get("year")
        if not year:
            continue
        # 简单年份过滤
        try:
            y = int(year)
            if y < since.year:
                continue
        except ValueError:
            continue

        title = paper.get("title", "")
        if not title:
            continue
        if EXCLUDE_RE.search(title):
            continue

        ext_ids = paper.get("externalIds", {})
        arxiv_id = ext_ids.get("ArXiv", "") or ext_ids.get("arXiv", "")
        authors = [a.get("name", "") for a in paper.get("authors", []) if a.get("name")]
        abstract = paper.get("abstract", "") or ""
        s2_url = paper.get("url", "")
        url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else s2_url
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else ""

        papers.append({
            "title": title.replace("\n", " "),
            "authors": ", ".join(authors[:4]),
            "abstract": abstract[:500],
            "url": url,
            "pdf": pdf_url,
            "published": str(year) if year else "???",
            "arxiv_id": arxiv_id,
            "source": "s2",
        })

    log(f"  Semantic Scholar 召回 {len(papers)} 篇")
    return papers


# ======================== 召回: 统一入口 ========================

def fetch_papers(since: datetime) -> list[dict]:
    """双路召回"""
    if DATA_SOURCE == "arxiv":
        log("数据源: arXiv (强制)")
        return fetch_from_arxiv(since)
    elif DATA_SOURCE == "s2":
        log("数据源: Semantic Scholar (强制)")
        return fetch_from_s2(since)
    else:
        # auto: 先 arXiv, 失败则 S2
        log("数据源: auto (先 arXiv, 失败则 Semantic Scholar)")
        papers = fetch_from_arxiv(since)
        if papers:
            return papers
        log("  arXiv 不可用, 启用 Semantic Scholar 兜底")
        return fetch_from_s2(since)


# ======================== 精排 ========================

def s2_cite(arxiv_id: str) -> int:
    """从 Semantic Scholar 查引用数"""
    if not arxiv_id:
        return 0
    url = f"https://api.semanticscholar.org/graph/v1/paper/ARXIV_ID:{arxiv_id}"
    r = _http_get(url, params={"fields": "citationCount"})
    if r is None:
        return 0
    try:
        return int(r.json().get("citationCount") or 0)
    except Exception:
        return 0

def score(p: dict) -> tuple[float, str]:
    txt = (p["title"] + " " + p.get("abstract", "")).lower()
    why, s = [], 0.0
    if "world action model" in txt or re.search(r"\bwam\b", txt):
        s += 4; why.append("核心范式")
    elif "world model" in txt and any(k in txt for k in ("action", "policy", "robot", "embodied")):
        s += 3; why.append("世界模型+动作")
    else:
        s += 1; why.append("弱相关")
    if any(k in txt for k in ("iclr", "icml", "neurips", "cvpr", "corl", "rss", "nvidia", "google deepmind")):
        s += 2; why.append("顶会/大厂")
    if len(p.get("abstract", "")) < 120:
        s -= 1
    return s, ", ".join(why)

def rank(papers: list[dict], top: int = TOP_N) -> list[dict]:
    scored = []
    for p in papers:
        s, why = score(p)
        c = s2_cite(p.get("arxiv_id", ""))
        scored.append((s + min(c / 5, 3), p, why, c))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, p, why, c in scored[:top]:
        out.append({
            "title": p["title"],
            "authors": p["authors"],
            "abstract": p.get("abstract", ""),
            "url": p["url"],
            "pdf": p.get("pdf", ""),
            "published": p["published"],
            "reason": why,
            "cite": c,
        })
    return out


# ======================== 飞书发送 ========================

def _sign() -> dict[str, str]:
    ts = str(int(time.time()))
    sg = base64.b64encode(
        hmac.new(f"{ts}\n{SECRET}".encode("utf-8"), digestmod=hashlib.sha256).digest()
    ).decode()
    return {"timestamp": ts, "sign": sg}

def _post(payload: dict) -> dict:
    if not WEBHOOK:
        log("未配置 FEISHU_WEBHOOK")
        return {"code": -1, "msg": "未配置 FEISHU_WEBHOOK"}
    body = json.dumps({**payload, **_sign()}, ensure_ascii=False)
    last = None
    for i in range(3):
        try:
            r = requests.post(WEBHOOK, data=body,
                              headers={"Content-Type": "application/json"}, timeout=8)
            resp = r.json()
            log(f"Feishu POST: status={r.status_code}, body={resp}")
            if resp.get("code") == 0:
                return resp
            if resp.get("code") == 11232:
                log(f"Feishu 限流(429), 等 {2 ** i + 1}s 后重试 ({i + 1}/3)")
                time.sleep(2 ** i + 1)
                continue
            return resp
        except Exception as e:
            last = e
            log(f"Feishu 请求异常: {type(e).__name__}: {e}")
            time.sleep(1)
    log(f"Feishu 最终失败: {last}")
    return {"code": -1, "msg": str(last)}

def card(title: str, elements: list[dict]) -> dict:
    return {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "turquoise"},
            "elements": elements,
        },
    }

def push(papers: list[dict]) -> list[dict]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    keyword = "WAM"
    sent_ok = []
    if not papers:
        resp = _post(card(f"{keyword} 本轮无新增论文", [
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"**{keyword} 时间**: {now}\n**说明**: 时间窗内无符合条件的新论文(不代表没有新工作,可能是关键词未覆盖)。"}},
        ]))
        log(f"空结果通知发送返回: {resp}")
        return sent_ok

    # 头部
    resp = _post(card(f"{keyword} 论文速递", [
        {"tag": "div", "text": {"tag": "lark_md",
         "content": f"**{keyword} | {datetime.now():%Y-%m-%d}  共 {len(papers)} 篇**\n按相关性排序,展示前 {len(papers)} 篇"}},
    ]))
    log(f"头部通知发送返回: {resp}")
    time.sleep(0.4)

    for i, p in enumerate(papers, 1):
        tags_parts = [p["reason"]]
        if p["cite"]:
            tags_parts.append(f"引用 {p['cite']}")
        tags_parts.append(p["published"])
        tags = " | ".join(tags_parts)

        title = f"{keyword} {i}/{len(papers)} | {p['title']}"
        body = f"{keyword} | {p['authors']}\n{tags}\n\n{p['abstract'] + ('...' if len(p.get('abstract', '')) >= 300 else '')}"

        resp = _post(card(title, [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**{keyword} | {p['title']}**"}},
            {"tag": "div", "text": {"tag": "lark_md",
             "content": body}},
            {"tag": "hr"},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "PDF"},
                 "type": "primary", "url": p.get("pdf", "")},
                {"tag": "button", "text": {"tag": "plain_text", "content": "arXiv"},
                 "type": "default", "url": p["url"]},
            ]},
        ]))
        log(f"论文 {i}/{len(papers)} 发送返回: {resp}")
        if resp.get("code") == 0:
            sent_ok.append(p)
        time.sleep(0.4)
    return sent_ok


# ======================== 主流程 ========================

def main(save_state: bool = True) -> None:
    backfill = float(os.environ.get("BACKFILL_DAYS", 0))
    since = datetime.utcnow() - timedelta(days=(backfill or WINDOW_DAYS))
    log(f"===== 开始 | 时间窗起点 {since.isoformat()} | backfill={backfill} =====")
    log(f"数据源模式: {DATA_SOURCE}")

    # 网络诊断
    log("--- 网络诊断 ---")
    for host in ["export.arxiv.org", "arxiv.org", "api.semanticscholar.org"]:
        try:
            r = requests.get(f"https://{host}", timeout=5)
            log(f"  {host}: OK ({r.status_code})")
        except Exception as e:
            log(f"  {host}: FAIL ({type(e).__name__})")

    papers = fetch_papers(since)
    log(f"召回 {len(papers)} 篇")

    seen = load_seen()
    new = [p for p in papers if p.get("arxiv_id", "") and p["arxiv_id"] not in seen]
    log(f"去重后 {len(new)} 篇")

    ranked = rank(new, top=TOP_N)
    log(f"去重后 {len(new)} 篇, 选中 {len(ranked)} 篇（最多 {TOP_N} 篇）, 准备推送")
    sent_ok = push(ranked)

    if save_state:
        # 只记录实际成功发送的论文
        for p in sent_ok:
            aid = p.get("arxiv_id", "")
            if aid:
                seen.add(aid)
        save_seen(seen)
    else:
        log("测试模式: 不更新 seen.json")
    log("完成")

if __name__ == "__main__":
    if "--reset-seen" in sys.argv or "-r" in sys.argv:
        reset_seen()
        raise SystemExit(0)
    save_state = "--no-save-seen" not in sys.argv
    log("=== WAM Paper Bot v2 启动 ===")
    log(f"Python: {sys.version}")
    try:
        main(save_state=save_state)
    except KeyboardInterrupt:
        log("用户中断")
    except Exception as e:
        log(f"未捕获异常: {e}")
        import traceback
        log(traceback.format_exc())
