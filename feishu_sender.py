# feishu_sender.py
import base64, hashlib, hmac, json, os, time

import requests

WEBHOOK = os.environ.get(
    "FEISHU_WEBHOOK",
    "https://open.feishu.cn/open-apis/bot/v2/hook/d85d61b1-6dd2-45b0-9775-b4387b22bf1c",
)
SECRET = os.environ.get(
    "FEISHU_SECRET",
    "dCQI0E4YtjypFT3ewvPp4",
)
MAX_BYTES = 18 * 1024  # 飞书请求体上限 20KB，留余量

def _sign() -> dict:
    """飞书签名：timestamp(秒,10位) + '\n' + secret -> HMAC-SHA256 -> base64"""
    ts = str(int(time.time()))
    body = f"{ts}\n{SECRET}"
    sign = base64.b64encode(
        hmac.new(body.encode("utf-8"), digestmod=hashlib.sha256).digest()
    ).decode()
    return {"timestamp": ts, "sign": sign}

def _post(payload: dict) -> dict:
    """统一出口：重试 + 超时 + 限流回退"""
    if not WEBHOOK:
        return {"code": -1, "msg": "未配置 WEBHOOK"}
    payload.update(_sign())
    for attempt in range(3):
        try:
            r = requests.post(
                WEBHOOK,
                data=json.dumps(payload, ensure_ascii=False),
                headers={"Content-Type": "application/json"},
                timeout=8,
            )
            resp = r.json()
            code = resp.get("code")
            if code == 0:
                return resp
            if code == 11232:                 # 频率限制：睡一下再试
                time.sleep(2 ** attempt + 1); continue
            if code == 19021:                 # 签名错 / 时间戳偏移 >1h，别重试了
                break
            return resp                       # 其他错误原样返回，方便排查
        except Exception as e:
            time.sleep(1)
            last_err = e
    return {"code": -1, "msg": str(last_err)}

# ---------- 卡片构建：一篇论文一张卡片 ----------
def paper_card(p: dict, idx: int, total: int) -> dict:
    tags = []
    if p.get("reason"): tags.append(p["reason"])
    if p.get("cite"):   tags.append(f"引用 {p['cite']}")
    tags.append(p["published"])
    tag_line = " · ".join(tags)

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📄 {idx}/{total}"},
                "template": "blue",          # blue/green/orange/red/carmine/violet...
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                  "content": f"**{p['title']}**"}},
                {"tag": "div", "text": {"tag": "lark_md",
                  "content": f"👤 {p['authors']}\n🏷️ {tag_line}"}},
                {"tag": "div", "text": {"tag": "lark_md",
                  "content": p["abstract"]}},
                {"tag": "hr"},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text","content":"PDF"},
                     "type": "primary", "url": p["pdf"]},
                    {"tag": "button", "text": {"tag": "plain_text","content":"arXiv"},
                     "type": "default", "url": p["url"]},
                ]},
            ],
        },
    }

def send_card(title: str, md_body: str) -> dict:
    """兜底用：单条富文本卡片"""
    return _post({
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text","content": title}, "template": "turquoise"},
            "elements": [{"tag": "div", "text": {"tag": "lark_md","content": md_body}}],
        },
    })

# ---------- 主推送：按字节切条，避免 20KB 超限 ----------
def push(papers: list[dict]) -> None:
    if not papers:
        _post(send_card("📭 本轮无新增", f"**WAM 论文速递**\n时间：{time.strftime('%Y-%m-%d %H:%M')}\n本轮无新增相关论文。"))
        return

    total = len(papers)
    header_md = f"**🤖 WAM 论文速递｜{time.strftime('%Y-%m-%d')}　共 {total} 篇**\n按相关性排序，最多展示 {total} 篇\n"

    buf_cards, buf_bytes = [], len(header_md.encode())
    def flush():
        if not buf_cards: return
        payload = send_card("🤖 WAM 论文速递", header_md)  # 这里用卡片数组版更规范，见下方说明
        # 为简洁起见，下面直接用多卡片顺序发送（飞书允许 5条/秒，我们间隔 0.3s）
        _post({"msg_type":"interactive","card":{
            "header":{"title":{"tag":"plain_text","content":"🤖 WAM 论文速递"},
                      "template":"turquoise"},
            "elements":[{"tag":"div","text":{"tag":"lark_md","content":header_md}}]+buf_cards}})
        buf_cards.clear(); time.sleep(0.35)

    for i, p in enumerate(papers, 1):
        card = paper_card(p, i, total)["card"]["elements"]
        size = sum(json.dumps(c, ensure_ascii=False).encode().__len__() for c in card)
        if buf_bytes + size > MAX_BYTES:
            flush(); buf_bytes = len(header_md.encode())
        buf_cards.extend(card); buf_bytes += size
    flush()
    send_card("ℹ️ 说明", "标签含义：核心范式 > 世界模型+动作 > 弱相关；引用数来自 Semantic Scholar，新论文通常为 0。")