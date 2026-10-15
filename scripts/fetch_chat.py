# -*- coding: utf-8 -*-
"""拉取指定微信联系人的聊天记录，产出清洗后的消息流与好感度基础统计。

用法:
  python fetch_chat.py --chat "昵称或备注" [--who 群成员昵称] [--max-msgs 5000]
                       [--out-dir ./profile_out] [--wc-cli PATH]

输出（out-dir 下）:
  messages.jsonl  每条: {time, sender, is_self, mtype, content}
  stats.json      基础统计（messages/stats 的量化原料，供好感度模型打分）
  contact.json    联系人详情（wechat-cli contacts --detail，失败则无此文件）

依赖 wechat-cli（密钥需已提取，见 ~/.wechat-cli/）。只读操作，不修改任何微信数据。
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime

DEFAULT_WC_CLI = r"C:/Users/ASUS/.workbuddy/binaries/python/envs/default/Scripts/wechat-cli.exe"
PAGE_SIZE = 1000
SESSION_GAP_MIN = 240  # 相邻消息间隔 >4h 视为新会话段
MAX_CONTENT_LEN = 500  # 单条消息截断长度（够 LLM 分析与报告引用）

MSG_LINE_RE = re.compile(r"^\[(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] (?P<sender>.+?): (?P<content>.*)$", re.S)
MEDIA_MARK_RE = re.compile(r"^\[(?P<mark>表情|图片|语音|视频|小程序|链接|文件|位置|通话|红包|转账)\]")
REPLY_XML_RE = re.compile(r"↳ 回复:\s*<msg>.*?<title>(?P<title>.*?)</title>", re.S)
REPLY_PLAIN_RE = re.compile(r"↳ 回复:\s*(?P<quote>.{0,100})", re.S)

# 轻量情感/行为词典。仅作量化信号，最终判断由 LLM 按 references/affinity-model.md 复核。
POS_WORDS = [
    "哈哈", "嘻嘻", "嘿嘿", "233", "xswl", "笑死", "喜欢", "爱你", "想你", "想念",
    "棒", "好耶", "开心", "高兴", "谢谢", "感谢", "感动", "暖心", "温暖", "亲",
    "宝贝", "抱抱", "厉害", "赞", "牛", "太强", "可爱", "舒服", "期待", "幸福",
    "晚安", "早安", "么么", "贴贴", "打call", "绝了", "优秀",
]
NEG_WORDS = [
    "烦", "讨厌", "生气", "气死", "难过", "伤心", "失望", "无语", "累", "哭",
    "郁闷", "烦躁", "滚", "有病", "吵架", "分歧", "算了", "随便", "不想说",
]
# 敷衍标记：单独成句的短回应（降 Engagement 信号，非情绪负面）
PERFUNCTORY = {"哦", "嗯", "好", "好的", "OK", "ok", "行", "呵呵", "嗯嗯", "收到", "了解", "知道了"}


def run_cli(wc_cli, args):
    r = subprocess.run([wc_cli] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"wechat-cli {' '.join(args)} 失败: {r.stderr.strip()[:300]}")
    return r.stdout


def parse_message(raw):
    """把 wechat-cli 的单条消息字符串解析为结构化 dict。失败返回 None。"""
    m = MSG_LINE_RE.match(raw)
    if not m:
        return None
    content = m.group("content").strip()
    mtype = "text"
    mm = MEDIA_MARK_RE.match(content)
    if mm:
        mark = mm.group("mark")
        mtype = {"表情": "sticker", "图片": "image", "语音": "voice", "视频": "video",
                 "小程序": "miniprogram", "链接": "link", "文件": "file", "位置": "location",
                 "通话": "call", "红包": "redpacket", "转账": "transfer"}[mark]
    # 清洗回复引用：XML 卡片提取 title，纯文本引用截断
    content = REPLY_XML_RE.sub(lambda x: f"↳回复[{x.group('title') or '链接/卡片'}]", content)
    content = REPLY_PLAIN_RE.sub(lambda x: f"↳回复[{x.group('quote').strip()}]", content)
    if len(content) > MAX_CONTENT_LEN:
        content = content[:MAX_CONTENT_LEN] + "…"
    sender = m.group("sender")
    return {
        "time": m.group("time"),
        "sender": sender,
        "is_self": sender == "me",
        "mtype": mtype,
        "content": content,
    }


def fetch_messages(wc_cli, chat, who, max_msgs, max_raw=30000):
    """分页拉取并解析。返回 (meta, messages)。

    max_raw 为原始消息拉取上限：群聊 + --who 场景下目标成员消息占比可能极低，
    无上限会长时间拉取不收敛（wechat-cli 单页群聊查询约 20s，大偏移更慢）。
    """
    messages, offset, raw_fetched = [], 0, 0
    meta, truncated = {}, False
    while True:
        remaining = max_msgs - len(messages)
        if remaining <= 0 or raw_fetched >= max_raw:
            truncated = raw_fetched >= max_raw
            break
        limit = min(PAGE_SIZE, remaining)
        out = run_cli(wc_cli, ["history", chat, "--limit", str(limit),
                               "--offset", str(offset), "--format", "json"])
        payload = json.loads(out)
        meta = {k: payload.get(k) for k in ("chat", "username", "is_group", "count", "failures")}
        raw_list = payload.get("messages") or []
        if not raw_list:
            break
        raw_fetched += len(raw_list)
        for raw in raw_list:
            pm = parse_message(raw)
            if pm is None:
                continue
            if who and not pm["is_self"] and pm["sender"] != who:
                continue  # 群聊场景只保留指定成员的消息
            messages.append(pm)
        if len(raw_list) < limit:
            break
        offset += len(raw_list)
    meta["truncated"] = truncated
    meta["raw_fetched"] = raw_fetched
    return meta, messages


def count_words(text, words):
    return sum(text.count(w) for w in words)


def per_side_stats(msgs):
    """分别统计我方 / 对方的消息行为。"""
    sides = {"self": [m for m in msgs if m["is_self"]],
             "other": [m for m in msgs if not m["is_self"]]}
    out = {}
    for side, arr in sides.items():
        texts = [m["content"] for m in arr if m["mtype"] == "text"]
        chars = sum(len(t) for t in texts)
        perfunct = sum(1 for t in texts if t in PERFUNCTORY)
        questions = sum(1 for t in texts if t.count("?") + t.count("？") > 0)
        out[side] = {
            "count": len(arr),
            "text_count": len(texts),
            "chars_total": chars,
            "avg_chars": round(chars / len(texts), 1) if texts else 0,
            "questions": questions,
            "perfunctory": perfunct,
            "pos_words": sum(count_words(t, POS_WORDS) for t in texts),
            "neg_words": sum(count_words(t, NEG_WORDS) for t in texts),
            "by_type": dict(Counter(m["mtype"] for m in arr)),
        }
    return out


def split_sessions(msgs, gap_min=SESSION_GAP_MIN):
    """按时间间隔切分会话段，返回 [(initiator_is_self, [msgs...]), ...]"""
    sessions = []
    cur, last = [], None
    for m in msgs:
        t = datetime.strptime(m["time"], "%Y-%m-%d %H:%M")
        if last and (t - last).total_seconds() > gap_min * 60:
            sessions.append(cur)
            cur = []
        cur.append(m)
        last = t
    if cur:
        sessions.append(cur)
    return sessions


def latency_buckets(latencies_min):
    dist = Counter()
    for v in latencies_min:
        if v < 5: dist["<5min"] += 1
        elif v < 30: dist["5-30min"] += 1
        elif v < 120: dist["30min-2h"] += 1
        elif v < 480: dist["2-8h"] += 1
        elif v < 1440: dist["8-24h"] += 1
        else: dist[">24h"] += 1
    avg = round(sum(latencies_min) / len(latencies_min), 1) if latencies_min else None
    return {"pairs": len(latencies_min), "avg_min": avg, "dist": dict(dist)}


def reply_latencies(msgs):
    """段内方向反转时的响应延迟：other_to_self=对方回应我方的速度（好感度核心），self_to_other=我方基线。"""
    o2s, s2o = [], []
    for sess in split_sessions(msgs):
        prev = None
        for m in sess:
            if prev and m["is_self"] != prev["is_self"]:
                dt = (datetime.strptime(m["time"], "%Y-%m-%d %H:%M")
                      - datetime.strptime(prev["time"], "%Y-%m-%d %H:%M")).total_seconds() / 60
                (s2o if m["is_self"] else o2s).append(dt)
            prev = m
    return {"other_to_self": latency_buckets(o2s), "self_to_other": latency_buckets(s2o)}


def hour_histogram(msgs):
    h = Counter(int(m["time"][11:13]) for m in msgs)
    return {str(i): h.get(i, 0) for i in range(24)}


def monthly_counts(msgs):
    mc = defaultdict(lambda: {"self": 0, "other": 0})
    for m in msgs:
        key = m["time"][:7]
        mc[key]["self" if m["is_self"] else "other"] += 1
    return dict(sorted(mc.items()))


def build_stats(meta, msgs):
    sides = per_side_stats(msgs)
    sessions = split_sessions(msgs)
    init_self = sum(1 for s in sessions if s and s[0]["is_self"])
    other_msgs = [m for m in msgs if not m["is_self"]]
    late_night = sum(1 for m in other_msgs if int(m["time"][11:13]) >= 23 or int(m["time"][11:13]) < 5)
    dates = sorted({m["time"][:10] for m in msgs})
    return {
        "chat": meta.get("chat"),
        "username": meta.get("username"),
        "is_group": meta.get("is_group"),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_msgs": len(msgs),
        "date_range": [dates[0], dates[-1]] if dates else None,
        "active_days": len(dates),
        "sessions": {"count": len(sessions), "self_initiated": init_self,
                     "other_initiated": len(sessions) - init_self},
        "sides": sides,
        "reply_latency": reply_latencies(msgs),
        "other_hour_histogram": hour_histogram(other_msgs),
        "other_late_night_ratio": round(late_night / len(other_msgs), 3) if other_msgs else 0,
        "monthly_counts": monthly_counts(msgs),
        "lexicon_note": "pos/neg/perfunctory 为轻量词典计数，仅作信号，须由 LLM 按 references/affinity-model.md 复核",
    }


def fetch_contact(wc_cli, chat):
    try:
        return json.loads(run_cli(wc_cli, ["contacts", "--detail", chat, "--format", "json"]))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="拉取微信聊天记录并计算好感度基础统计")
    ap.add_argument("--chat", required=True, help="联系人昵称/备注/群名（与 wechat-cli history 一致）")
    ap.add_argument("--who", default=None, help="群聊中指定成员昵称（只统计该成员作为'对方'）")
    ap.add_argument("--max-msgs", type=int, default=5000, help="消息条数上限（默认 5000）")
    ap.add_argument("--out-dir", default="./profile_out", help="输出目录")
    ap.add_argument("--wc-cli", default=os.environ.get("WECHAT_CLI", DEFAULT_WC_CLI))
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    meta, msgs = fetch_messages(args.wc_cli, args.chat, args.who, args.max_msgs)
    if not msgs:
        print(json.dumps({"error": "未拉取到消息", "meta": meta}, ensure_ascii=False))
        sys.exit(1)

    # 按时间正序（wechat-cli 返回倒序）
    msgs.sort(key=lambda m: m["time"])
    with open(os.path.join(args.out_dir, "messages.jsonl"), "w", encoding="utf-8") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    stats = build_stats(meta, msgs)
    stats["who"] = args.who
    stats["truncated"] = meta.get("truncated", False)
    stats["raw_fetched"] = meta.get("raw_fetched", len(msgs))
    if stats["truncated"]:
        stats["truncation_note"] = (
            "原始消息拉取达到上限，统计仅覆盖部分时间范围；"
            "群聊+指定成员场景建议改用私聊分析或降低 max-msgs")
    with open(os.path.join(args.out_dir, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    contact = fetch_contact(args.wc_cli, args.chat)
    if contact:
        with open(os.path.join(args.out_dir, "contact.json"), "w", encoding="utf-8") as f:
            json.dump(contact, f, ensure_ascii=False, indent=2)

    print(json.dumps({"ok": True, "out_dir": os.path.abspath(args.out_dir),
                      "total_msgs": len(msgs), "date_range": stats["date_range"],
                      "sessions": stats["sessions"], "truncated": stats["truncated"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
