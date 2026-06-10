#!/usr/bin/env python3
"""攒钱修仙 - 省钱 RPG 引擎 v4

只负责数据存取与境界/灵石/道心/成就/灵根加成计算，叙事交给 Claude。
金额单位：元。修为单位：点。
  · 实际攒下(元) = 真实省下的钱，始终诚实显示，是不动的锚。
  · 修为(点)     = 基础节省 + 灵根亲和加成；亲和品类省下的钱 ×1.5 计入修为。
  · 境界阈值以「修为点」衡量。
两套预算：
  · 每日预算(budgets/records)        —— 每天循环结算(早餐/午餐/通勤…)。
  · 月度 bonus 预算(monthly_*)       —— 旅游/宠物等非每日开支，按月结算：
       平时只累计本月已花/剩余；跨月后自动结算上月省下的部分→修为。
       仅当月录入过的项才结算(没录入=不计，与每日同理)。
数据默认存 ~/.claude/savings-rpg/data.json，可用环境变量 SAVINGS_RPG_DIR 覆盖。
"""

import argparse
import json
import os
import random
import sys
from datetime import datetime, date, timedelta

DATA_DIR = os.environ.get(
    "SAVINGS_RPG_DIR", os.path.expanduser("~/.claude/savings-rpg")
)
DATA_FILE = os.path.join(DATA_DIR, "data.json")

# ── 修仙大境界：name, 进入所需累计修为(点) ──
REALMS = [
    ("练气期", 0),
    ("筑基期", 200),
    ("金丹期", 600),
    ("元婴期", 1500),
    ("化神期", 3000),
    ("炼虚期", 6000),
    ("合体期", 12000),
    ("大乘期", 24000),
    ("渡劫期", 50000),
    ("飞升", 100000),
]
SUB_STAGES = ["初期", "中期", "后期", "大圆满"]
MINOR_TRIBULATION_FROM = 4   # 化神期起，大突破历天劫
GREAT_TRIBULATION_FROM = 8   # 渡劫期/飞升 为大天劫

# ── 消费品类 ──
CATEGORIES = ["饮食", "出行", "购物", "玩乐", "居家", "其他"]

# ── 五行灵根：key -> (名称, 道心箴言, 亲和品类) ──
# 亲和品类省下的钱享 BONUS 倍修为加成。
ROOTS = {
    "金": ("金灵根", "锋锐果决，斩断冗费如利刃出鞘。", "购物"),
    "木": ("木灵根", "生生不息，细水长流以成参天。", "居家"),
    "水": ("水灵根", "柔韧顺势，随形而省，无孔不入。", "出行"),
    "火": ("火灵根", "炽烈进取，克制最炽热的玩乐之欲。", "玩乐"),
    "土": ("土灵根", "厚重稳健，吃土有道，积土成山。", "饮食"),
}
BONUS = 1.5  # 亲和品类修为加成倍率

# ── 灵石坊市：随机灵石商人（上传截图=向商人换灵石，增强仪式感）──
# 每笔省下的钱在坊市换成等额灵石(1元=1灵石，诚信锚)，再炼化为修为。
# 商人由记录内容确定性选出(同一笔永远同一个商人，不随重跑变化)。
MERCHANTS = [
    ("青石翁", "坊市最老的散修，灵石成色足，话不多。"),
    ("赤眉商客", "走南闯北的行脚商，眼尖，最爱与节俭之人交易。"),
    ("白裳道姑", "云游售石的散仙，称省下的每一文都是“干净灵石”。"),
    ("墨袍鬼市掌柜", "只在月隐之夜开摊，灵石幽光内蕴。"),
    ("黄牙老矿工", "亲手在灵脉里凿石，最敬肯省的修士。"),
    ("碧眼胡商", "异域来客，灵石带异香，换石必添一句吉言。"),
    ("瘸腿剑奴", "昔年剑修，败于挥霍，如今守摊劝人惜财。"),
    ("锦衣少东家", "坊市大族子弟，偏爱与逆境修士结缘。"),
    ("灰袍藏经老者", "藏经阁出身，换石时总要考问一句俭道。"),
    ("断手炼器师", "炼器伤了手，改行售石，灵石棱角分明。"),
]


def pick_merchant(seed_str):
    """按记录内容确定性挑一个灵石商人，保证同一笔记录稳定。"""
    rng = random.Random(seed_str)
    return rng.choice(MERCHANTS)


# ── 随机奇遇：验证录入后有概率触发的限时省钱任务 ──
QUEST_CHANCE = 0.35  # 每次「带截图的录入」后触发奇遇的概率

# 奇遇库: (id, 名称, 试炼描述, 品类, 期限天数, 奖励灵石)
QUESTS = [
    ("bigu",   "辟谷之试", "限内择一日不点外卖、不下馆子，亲手烹一餐果腹。", "饮食", 3, 5),
    ("zhinai", "止戈奶茶", "限内一杯奶茶、咖啡皆不沾口，斩断糖饮之欲。", "饮食", 5, 6),
    ("jianshan","俭膳之约", "限内有一顿正餐，花费压到平日午餐预算的一半以下。", "饮食", 3, 5),
    ("qingdun","清囤渡厄", "限内清点冰箱囤货，用掉一样将坏的食材，省下一笔。", "饮食", 3, 4),
    ("tubu",   "徒步问道", "限内有一程通勤舍打车、改步行/骑行/公交，省下脚力钱。", "出行", 3, 4),
    ("kongnang","空囊禁购", "限内不在网上购任何非必需之物，按住剁手之手。", "购物", 7, 8),
    ("juzeng", "拒赠之心", "限内拒绝一次「免费/赠品/满减凑单」的诱惑。", "购物", 3, 4),
    ("biguan", "闭关绝玩", "限内不为游戏娱乐充值、不冲动购票，静心闭关。", "玩乐", 5, 6),
    ("zhanrong","斩冗一物", "限内退订或取消一项久未用的订阅、会员。", "居家", 3, 7),
    ("wandi",  "一日完璧", "限内择一日，把所有预算场景全数录入且当日净省下。", "综合", 2, 5),
    ("xuxiu",  "连修续脉", "自今日起连修三日不断更，养你道心。", "综合", 3, 5),
    ("shichuan","水滴石穿", "限内每日皆有净省（哪怕只省一元），日日不空。", "综合", 5, 7),
]


def roll_quest(data, seed_str, base_date_str):
    """带截图录入后掷骰：无在途奇遇时按概率触发，返回新奇遇 dict 或 None。
    用记录内容做种子，保证同一笔记录结果稳定。"""
    if data["quests"].get("active"):
        return None
    rng = random.Random("quest|" + seed_str)
    if rng.random() >= QUEST_CHANCE:
        return None
    qid, name, desc, cat, days, reward = rng.choice(QUESTS)
    base = date.fromisoformat(base_date_str)
    due = (base + timedelta(days=days)).isoformat()
    q = {"id": qid, "name": name, "desc": desc, "category": cat,
         "reward": reward, "days": days,
         "created_date": base_date_str, "due_date": due}
    data["quests"]["active"] = q
    return q


def expire_quests(data, today_iso):
    """若在途奇遇已过期(今日 > 截止)，移入 log(expired) 并返回它；否则 None。"""
    q = data["quests"].get("active")
    if not q:
        return None
    if today_iso > q["due_date"]:
        data["quests"]["active"] = None
        data["quests"]["log"].append({**q, "outcome": "expired",
                                      "closed_date": today_iso})
        return q
    return None


def quest_reward_total(data):
    """已达成奇遇累计奖励的灵石数。"""
    return sum(e["reward"] for e in data.get("quests", {}).get("log", [])
               if e.get("outcome") == "claimed")

# 场景名 -> 品类 的关键词推断
CATEGORY_KEYWORDS = {
    "饮食": ["早餐", "午餐", "晚餐", "夜宵", "餐", "饭", "零食", "吃", "奶茶",
             "咖啡", "外卖", "食", "水果", "饮料"],
    "出行": ["通勤", "打车", "地铁", "公交", "油", "加油", "停车", "高铁",
             "火车", "机票", "出行", "车", "路费", "过路"],
    "购物": ["购物", "买", "衣", "鞋", "数码", "网购", "淘宝", "包", "化妆",
             "护肤", "电子"],
    "玩乐": ["玩", "娱乐", "游戏", "电影", "ktv", "唱歌", "酒", "聚会", "旅游",
             "旅行", "玩乐", "演出", "门票"],
    "居家": ["房租", "水电", "日用", "家", "话费", "宽带", "网费", "清洁",
             "纸巾", "居家", "物业"],
}


def today_str():
    return date.today().isoformat()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def infer_category(scene):
    s = scene.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in s:
                return cat
    return "其他"


def load():
    if not os.path.exists(DATA_FILE):
        return None
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("root", "")
    data.setdefault("achievements", [])
    # 月度 bonus 预算/记录/已结算
    data.setdefault("monthly_budgets", {})   # {scene: {amount, category}}
    data.setdefault("monthly_spend", [])     # [{month,scene,category,amount,...}]
    data.setdefault("monthly_settled", [])   # [{month,scene,category,budget,spent,saved}]
    # 随机奇遇：{active: 在途任务 or None, log: 已结束任务列表}
    q = data.setdefault("quests", {"active": None, "log": []})
    q.setdefault("active", None)
    q.setdefault("log", [])
    # 迁移旧版每日预算格式 {场景: 金额} -> {场景: {amount, category}}
    migrated = {}
    for scene, val in data.get("budgets", {}).items():
        if isinstance(val, dict):
            migrated[scene] = val
        else:
            migrated[scene] = {"amount": float(val),
                               "category": infer_category(scene)}
    data["budgets"] = migrated
    # 同样迁移月度预算（防御）
    mmig = {}
    for scene, val in data.get("monthly_budgets", {}).items():
        if isinstance(val, dict):
            mmig[scene] = val
        else:
            mmig[scene] = {"amount": float(val),
                           "category": infer_category(scene)}
    data["monthly_budgets"] = mmig
    return data


def save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def require(data):
    if data is None:
        print("⚠️ 尚未开始修炼。请先运行 init 开辟道场、择定灵根，再用 budget 设定每日预算。")
        sys.exit(1)


def fmt_money(x):
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x))}"
    return f"{x:.2f}"


def budget_amount(data, scene):
    b = data["budgets"].get(scene)
    return b["amount"] if b else None


def budget_category(data, scene):
    b = data["budgets"].get(scene)
    return b["category"] if b else infer_category(scene)


def affinity_category(data):
    root = data.get("root")
    return ROOTS[root][2] if root in ROOTS else None


def record_category(data, r):
    return r.get("category") or budget_category(data, r["scene"])


# ── 月度 bonus 预算 ──

def this_month():
    return date.today().strftime("%Y-%m")


def settle_past_months(data):
    """跨月自动结算：把已结束月份里、当月录入过的 bonus 项结算成修为。
    只结算「严格早于当前月」且未结算过的 (月,场景)。返回新结算条目列表。"""
    cur = this_month()
    settled_keys = {(s["month"], s["scene"]) for s in data["monthly_settled"]}
    # 按 (月,场景) 聚合当月开支
    agg = {}
    for sp in data["monthly_spend"]:
        m = sp["month"]
        if m >= cur:
            continue  # 当前月或未来月不结算
        key = (m, sp["scene"])
        agg.setdefault(key, 0.0)
        agg[key] += sp["amount"]

    new_settles = []
    for (m, scene), spent in sorted(agg.items()):
        if (m, scene) in settled_keys:
            continue
        b = data["monthly_budgets"].get(scene)
        if not b:
            continue  # 预算项已删，跳过
        budget = b["amount"]
        category = b["category"]
        saved = budget - spent
        entry = {"month": m, "scene": scene, "category": category,
                 "budget": budget, "spent": spent, "saved": saved,
                 "settled_at": now_str()}
        data["monthly_settled"].append(entry)
        new_settles.append(entry)
    return new_settles


def month_progress(data, month=None):
    """返回 {scene: {budget, spent, remaining, category, recorded}} 针对某月(默认本月)。"""
    month = month or this_month()
    out = {}
    for scene, b in data["monthly_budgets"].items():
        out[scene] = {"budget": b["amount"], "category": b["category"],
                      "spent": 0.0, "recorded": False}
    for sp in data["monthly_spend"]:
        if sp["month"] != month:
            continue
        if sp["scene"] in out:
            out[sp["scene"]]["spent"] += sp["amount"]
            out[sp["scene"]]["recorded"] = True
    for scene in out:
        out[scene]["remaining"] = out[scene]["budget"] - out[scene]["spent"]
    return out


# ── 境界计算 ──

def realm_of(xp):
    idx = 0
    for i, (name, th) in enumerate(REALMS):
        if xp >= th:
            idx = i
        else:
            break
    realm = REALMS[idx][0]
    floor = REALMS[idx][1]
    if idx + 1 < len(REALMS):
        nxt_name = REALMS[idx + 1][0]
        nxt_th = REALMS[idx + 1][1]
        band = (nxt_th - floor) / 4.0
        sub_idx = int((xp - floor) / band) if band > 0 else 3
        sub_idx = max(0, min(3, sub_idx))
        sub = SUB_STAGES[sub_idx]
    else:
        nxt_name, nxt_th = None, None
        sub, sub_idx = "", 3
    return {"idx": idx, "realm": realm, "floor": floor,
            "next_name": nxt_name, "next_th": nxt_th,
            "sub": sub, "sub_idx": sub_idx}


def full_title(xp):
    r = realm_of(xp)
    if r["next_name"] is None:
        return r["realm"]
    return f"{r['realm']}·{r['sub']}"


def has_started(data):
    """是否已踏入仙途：录过任意一笔日常记录或结算过月度项。"""
    return bool(data.get("records")) or bool(data.get("monthly_settled"))


def display_title(data, stats, xp):
    """未入门(零记录)显示「凡人·未入门」，否则正常境界。
    第一笔记录即从凡人突破入练气期，构成首次小境界突破。"""
    if stats["total_records"] == 0 and not data.get("monthly_settled"):
        return "凡人·未入门"
    return full_title(xp)


def progress_bar(xp):
    r = realm_of(xp)
    if r["next_th"] is None:
        return "█" * 20 + f" 已臻化境 · 修为 {fmt_money(xp)} 点"
    span = r["next_th"] - r["floor"]
    done = xp - r["floor"]
    ratio = max(0.0, min(1.0, done / span)) if span > 0 else 1.0
    filled = int(round(ratio * 20))
    bar = "█" * filled + "░" * (20 - filled)
    return (f"{bar} {ratio*100:.1f}%  距【{r['next_name']}】还差 "
            f"{fmt_money(r['next_th'] - xp)} 点修为")


# ── 道心吸收效率系数 ──
# 道心(连修)越稳，省下的灵石炼化为修为的效率越高；断更重修则系数<1。
# 实际攒下的钱(元)不受影响，系数只作用于「灵石→修为」的炼化。

def daoxin_state(run_len, has_prior):
    """按截至某日的连修天数返回 (道心称谓, 吸收效率系数)。
    run_len: 含当日的连修天数；has_prior: 此日之前是否已有记录(用于区分首修与断更重修)。"""
    if run_len >= 7:
        return ("道心通明", 1.2)
    if run_len >= 3:
        return ("道心渐稳", 1.1)
    if run_len == 1 and has_prior:
        return ("道心不稳", 0.9)   # 断更后重修，效率折损
    return ("初窥门径", 1.0)        # 连修 1-2 天 / 生平首笔，不折损


def daoxin_by_streak(cur_streak):
    """按 current_streak 给 (状态, 系数)，用于 status 展示下一笔的预期吸收效率。"""
    if cur_streak >= 7:
        return ("道心通明", 1.2)
    if cur_streak >= 3:
        return ("道心渐稳", 1.1)
    if cur_streak >= 1:
        return ("初窥门径", 1.0)
    return ("道心不稳", 0.9)


def streak_runs(dates):
    """给定已录入日期列表，返回 {date: (run_len, has_prior)}。"""
    ds = sorted(set(dates))
    dset = set(ds)
    out = {}
    for i, d in enumerate(ds):
        dd = date.fromisoformat(d)
        run = 1
        cur = dd - timedelta(days=1)
        while cur.isoformat() in dset:
            run += 1
            cur -= timedelta(days=1)
        out[d] = (run, i > 0)
    return out


# ── 单笔修为计算（省下 × 灵根亲和倍率 × 道心吸收系数）──

def affinity_mult(data, category):
    aff = affinity_category(data)
    return BONUS if (aff and category == aff) else 1.0


def record_xp_gain(data, saved, category, daoxin_coef=1.0):
    """返回该笔记录贡献的修为(点)。
    省下: saved × 灵根亲和倍率 × 道心吸收系数；超支(负): 原样扣除，不受系数影响。"""
    if saved < 0:
        return saved
    return saved * affinity_mult(data, category) * daoxin_coef


# ── 统计/streak/成就 ──

def compute_stats(data):
    total_saved = 0.0     # 实际省下(元)，真实锚
    total_loss = 0.0      # 实际超支(元)
    xp = 0.0              # 修为(点)，含灵根加成 + 道心系数
    aff_extra = 0.0       # 灵根亲和多出来的修为(点)
    daoxin_extra = 0.0    # 道心系数带来的修为增减(点，可负)
    aff = affinity_category(data)

    # 每个录入日的道心系数(按截至该日的连修锁定)
    rec_dates = [r["date"] for r in data["records"]]
    runs = streak_runs(rec_dates)
    coef_of = {}
    for d, (run, has_prior) in runs.items():
        coef_of[d] = daoxin_state(run, has_prior)[1]

    for r in data["records"]:
        diff = r["planned"] - r["actual"]
        cat = record_category(data, r)
        if diff >= 0:
            total_saved += diff
            coef = coef_of.get(r["date"], 1.0)
            amult = affinity_mult(data, cat)
            after_aff = diff * amult
            gain = after_aff * coef
            xp += gain
            aff_extra += (after_aff - diff)
            daoxin_extra += (gain - after_aff)
        else:
            total_loss += -diff
            xp += diff

    # 已结算的月度 bonus 预算：享灵根加成，但不计道心(道心是每日功课)
    for s in data.get("monthly_settled", []):
        diff = s["saved"]
        cat = s.get("category", "其他")
        if diff >= 0:
            total_saved += diff
            after_aff = diff * affinity_mult(data, cat)
            xp += after_aff
            aff_extra += (after_aff - diff)
        else:
            total_loss += -diff
            xp += diff

    bonus_extra = aff_extra + daoxin_extra

    net_real = total_saved - total_loss

    day_net = {}
    for r in data["records"]:
        day_net[r["date"]] = day_net.get(r["date"], 0.0) + r["saved"]
    dates = sorted(day_net.keys())

    max_streak = 0
    cur_run = 0
    prev = None
    for d in dates:
        dd = date.fromisoformat(d)
        if prev is not None and dd - prev == timedelta(days=1):
            cur_run += 1
        else:
            cur_run = 1
        max_streak = max(max_streak, cur_run)
        prev = dd

    current_streak = 0
    if dates:
        dset = set(dates)
        cursor = date.today()
        if cursor.isoformat() not in dset and (cursor - timedelta(days=1)).isoformat() in dset:
            cursor = cursor - timedelta(days=1)
        while cursor.isoformat() in dset:
            current_streak += 1
            cursor -= timedelta(days=1)

    had_perfect_day = False
    budgets = data.get("budgets", {})
    if budgets:
        for d in dates:
            recs = [r for r in data["records"] if r["date"] == d]
            scenes = {r["scene"] for r in recs}
            if set(budgets.keys()).issubset(scenes):
                if all(r["saved"] >= 0 for r in recs if r["scene"] in budgets):
                    had_perfect_day = True
                    break

    had_comeback = False
    seen_down = False
    for d in dates:
        if day_net[d] < 0:
            seen_down = True
        elif day_net[d] > 0 and seen_down:
            had_comeback = True
            break

    return {
        "xp": xp,
        "bonus_extra": bonus_extra,
        "aff_extra": aff_extra,
        "daoxin_extra": daoxin_extra,
        "net_real": net_real,
        "total_saved": total_saved,
        "total_loss": total_loss,
        "total_records": len(data["records"]),
        "cultivation_days": len(dates),
        "max_streak": max_streak,
        "current_streak": current_streak,
        "had_perfect_day": had_perfect_day,
        "had_comeback": had_comeback,
        "affinity": aff,
    }


def _ach_defs():
    return [
        ("初入江湖", "录入第一笔修炼记录", 1,
         lambda s: s["total_records"] >= 1),
        ("开源节流", "累计省下 100 元", 2,
         lambda s: s["total_saved"] >= 100),
        ("聚沙成塔", "累计省下 1000 元", 10,
         lambda s: s["total_saved"] >= 1000),
        ("富甲一方", "累计省下 10000 元", 50,
         lambda s: s["total_saved"] >= 10000),
        ("三日不辍", "连续修炼 3 天", 3,
         lambda s: s["max_streak"] >= 3),
        ("七星连珠", "连续修炼 7 天", 7,
         lambda s: s["max_streak"] >= 7),
        ("月度真人", "连续修炼 30 天", 20,
         lambda s: s["max_streak"] >= 30),
        ("完璧之日", "某日所有预算场景全部录入且净省下", 5,
         lambda s: s["had_perfect_day"]),
        ("浪子回头", "在净修为下跌后的某天重回净省", 3,
         lambda s: s["had_comeback"]),
    ]


def eval_achievements(stats):
    return [(n, d, r, bool(c(stats))) for (n, d, r, c) in _ach_defs()]


def spirit_stones(data, stats):
    ach = eval_achievements(stats)
    ach_reward = sum(r for (_, _, r, ok) in ach if ok)
    return stats["cultivation_days"] + ach_reward + quest_reward_total(data)


def records_on(data, day):
    return [r for r in data["records"] if r["date"] == day]


# ── 子命令 ──

def _validate_root(root_key):
    root_key = (root_key or "").strip()
    if root_key and root_key not in ROOTS:
        print(f"未知灵根 '{root_key}'，可选：{'/'.join(ROOTS.keys())}（金木水火土）。")
        sys.exit(1)
    return root_key


def _announce_root(root_name):
    nm, motto, aff = ROOTS[root_name]
    print(f"   你身负【{nm}】——{motto}")
    print(f"   命定战场【{aff}】：此类省下的钱，修为加成 ×{BONUS}！灵根天定，此生不改。")


def cmd_init(args):
    data = load()
    root_key = _validate_root(args.root)

    # 道场已存在
    if data is not None and not args.force:
        if data.get("root"):
            nm = ROOTS[data["root"]][0]
            print(f"道场已立，灵根天定不可更改（当前【{nm}】）。")
            print("如要推倒重来、重测灵根，请加 --force（会清空所有记录、灵根、成就）。")
            return
        # 旧档/空灵根：允许补测一次，不清数据
        if root_key:
            data["root"] = root_key
            save(data)
            print("✨ 灵根已测定，铭刻入道籍：")
            _announce_root(root_key)
        else:
            print("道场已存在但尚未测定灵根。请先完成测灵根，再 init --root 金/木/水/火/土 补录。")
        return

    # 新建道场（或 --force 重置）
    data = {"created_at": now_str(), "root": root_key,
            "budgets": {}, "records": [], "achievements": [],
            "monthly_budgets": {}, "monthly_spend": [], "monthly_settled": [],
            "quests": {"active": None, "log": []}}
    save(data)
    if root_key:
        print("✅ 道场已开辟，测灵根结果铭刻入道籍：")
        _announce_root(root_key)
    else:
        print("✅ 道场已开辟（尚未测定灵根，完成测试后 init --root 金/木/水/火/土 补录）。")
    print("下一步：用 budget 设定每日各场景的开销预算。")


def _print_settlements(settles):
    """渲染跨月自动结算结果。"""
    if not settles:
        return
    print("📅 跨月结算（上月秘境清算）:")
    for s in settles:
        diff = s["saved"]
        if diff >= 0:
            print(f"   🏔️ {s['month']}【{s['scene']}·{s['category']}】月额 "
                  f"{fmt_money(s['budget'])} 元，实花 {fmt_money(s['spent'])} 元 "
                  f"→ 省下 {fmt_money(diff)} 元，化作修为。")
        else:
            print(f"   ⚔️ {s['month']}【{s['scene']}·{s['category']}】月额 "
                  f"{fmt_money(s['budget'])} 元，实花 {fmt_money(s['spent'])} 元 "
                  f"→ 超支 {fmt_money(-diff)} 元，心魔反噬！")


def cmd_budget(args):
    data = load()
    require(data)
    aff = affinity_category(data)
    if args.show or not args.items:
        if not data["budgets"]:
            print("尚未设定预算。示例：budget 早餐=10:饮食 午餐=30 通勤=40:出行 零食=20")
            print(f"格式 场景=金额[:品类]；品类可省略(自动推断)。可选品类: {'/'.join(CATEGORIES)}")
            return
        total = sum(b["amount"] for b in data["budgets"].values())
        print("📜 每日预算（道心戒律）:")
        for scene, b in data["budgets"].items():
            star = " ★亲和" if aff and b["category"] == aff else ""
            print(f"  · {scene}: {fmt_money(b['amount'])} 元 [{b['category']}]{star}")
        print(f"  ── 每日预算合计: {fmt_money(total)} 元")
        if aff:
            print(f"  （★ 标记为你的亲和品类【{aff}】，省下修为 ×{BONUS}）")
        return
    for item in args.items:
        if "=" not in item:
            print(f"忽略无法解析的项: {item}（应为 场景=金额[:品类]）")
            continue
        scene, rest = item.split("=", 1)
        scene = scene.strip()
        cat = None
        if ":" in rest:
            amt_s, cat = rest.split(":", 1)
            cat = cat.strip()
            if cat not in CATEGORIES:
                print(f"  ⚠️ 品类 '{cat}' 非法，自动推断。可选: {'/'.join(CATEGORIES)}")
                cat = None
        else:
            amt_s = rest
        try:
            amt = float(amt_s)
        except ValueError:
            print(f"忽略无法解析的金额: {item}")
            continue
        if cat is None:
            cat = infer_category(scene)
        data["budgets"][scene] = {"amount": amt, "category": cat}
    save(data)
    total = sum(b["amount"] for b in data["budgets"].values())
    print("✅ 预算已更新（道心戒律）:")
    for scene, b in data["budgets"].items():
        star = " ★亲和" if aff and b["category"] == aff else ""
        print(f"  · {scene}: {fmt_money(b['amount'])} 元 [{b['category']}]{star}")
    print(f"  ── 每日预算合计: {fmt_money(total)} 元")


def cmd_record(args):
    data = load()
    require(data)
    scene = args.scene.strip()
    if scene not in data["budgets"]:
        print(f"⚠️ 场景【{scene}】不在每日预算表里。已有场景: "
              f"{', '.join(data['budgets'].keys()) or '（空）'}")
        if scene in data["monthly_budgets"]:
            print(f"提示：【{scene}】是月度 bonus 预算项，请用 spend 命令录入。")
        else:
            print("可先用 budget 把它加进预算，或检查场景名是否写错。")
        sys.exit(1)

    settles = settle_past_months(data)
    _print_settlements(settles)
    rec_date = args.date or today_str()
    expired = expire_quests(data, rec_date)
    if expired:
        print(f"⌛ 奇遇【{expired['name']}】已过期（截止 {expired['due_date']}），"
              f"未能在限内完成，{expired['reward']} 灵石奖励落空。莫气馁，下回再遇。")
    stats_before = compute_stats(data)
    xp_before = stats_before["xp"]
    started_before = has_started(data)
    title_before = display_title(data, stats_before, xp_before)
    realm_idx_before = realm_of(xp_before)["idx"]
    ach_before = {n for (n, _, _, ok) in eval_achievements(stats_before) if ok}

    planned = budget_amount(data, scene)
    category = budget_category(data, scene)
    actual = float(args.actual)
    diff = planned - actual
    rec = {"date": args.date or today_str(), "scene": scene,
           "category": category, "planned": planned, "actual": actual,
           "saved": diff, "proof": args.proof or "",
           "verified": bool(args.proof), "note": args.note or "",
           "timestamp": now_str()}
    data["records"].append(rec)

    stats_after = compute_stats(data)
    xp = stats_after["xp"]
    title_after = display_title(data, stats_after, xp)
    realm_idx_after = realm_of(xp)["idx"]
    ach_after_list = eval_achievements(stats_after)
    new_ach = {n for (n, _, _, ok) in ach_after_list if ok} - ach_before
    data["achievements"] = sorted(n for (n, _, _, ok) in ach_after_list if ok)
    save(data)

    aff = affinity_category(data)
    is_aff = bool(aff and category == aff)
    # 本笔记录所在日的道心系数（按插入后的连修锁定）
    rday = rec["date"]
    rruns = streak_runs([r["date"] for r in data["records"]])
    run_len, has_prior = rruns.get(rday, (1, False))
    dx_name, dx_coef = daoxin_state(run_len, has_prior)
    amult = affinity_mult(data, category)
    gain = (diff * amult * dx_coef) if diff >= 0 else diff

    if diff > 0:
        mname, mdesc = pick_merchant(
            f"{rec['date']}|{scene}|{actual}|{rec['timestamp']}")
        proof_tag = "（凭支付截图为信物）" if args.proof else "（口头交易·无凭，灵石虚浮）"
        print(f"🏪 灵石坊市：你寻到【{mname}】{proof_tag}，"
              f"以省下的 {fmt_money(diff)} 元换得 {fmt_money(diff)} 枚灵石。")
        print(f"   （{mname}：{mdesc}）")
        line = (f"🌿 炼化灵石 → 【{scene}·{category}】省下 {fmt_money(diff)} 元")
        factors = []
        if is_aff:
            root_nm = ROOTS[data['root']][0]
            factors.append(f"{root_nm}亲和【{category}】×{amult}")
        factors.append(f"{dx_name}吸收×{dx_coef}")
        line += "，" + "、".join(factors) + f" → 炼化修为 +{fmt_money(gain)} 点。"
        print(line)
        if dx_coef != 1.0:
            if dx_coef > 1:
                print(f"   （{dx_name}，灵石吸收效率提至 {dx_coef}，省下的灵气炼化更足。）")
            else:
                print(f"   （{dx_name}：断更后初修，吸收效率折至 {dx_coef}，连修养回即升。）")
    elif diff == 0:
        print(f"⚖️ 【{scene}·{category}】恰好花满 {fmt_money(planned)} 元，无功无过。")
    else:
        print(f"⚔️ 【{scene}·{category}】预算 {fmt_money(planned)} 元，实花 "
              f"{fmt_money(actual)} 元 → 超支 {fmt_money(-diff)} 元，心魔反噬，修为 -{fmt_money(-diff)} 点！")

    print(f"   当前修为: {fmt_money(xp)} 点 · 境界【{title_after}】 "
          f"· 实际累计攒下 {fmt_money(stats_after['net_real'])} 元")
    print(f"   {progress_bar(xp)}")

    if not started_before and has_started(data):
        print("   ✨ 首次破境！你由【凡人】踏入【练气期·初期】，自此正式步入仙途！"
              "（首笔记录必入此境，无论金额多寡）")
    elif title_after != title_before:
        if realm_idx_after > realm_idx_before:
            if realm_idx_after >= GREAT_TRIBULATION_FROM:
                print(f"   ⚡⚡ 大天劫降临！你强渡天劫，跻身【{REALMS[realm_idx_after][0]}】！")
            elif realm_idx_after >= MINOR_TRIBULATION_FROM:
                print(f"   ⚡ 天劫将至，你顶住雷霆，突破至【{REALMS[realm_idx_after][0]}】！")
            else:
                print(f"   ✨ 大境界突破！晋入【{REALMS[realm_idx_after][0]}】！")
        else:
            sub_now = realm_of(xp)["sub"]
            print(f"   ✨ 修为精进，小境界突破至【{title_after}】！（小层：{sub_now}）")

    for (n, d, rwd, ok) in ach_after_list:
        if n in new_ach:
            print(f"   🏅 解锁道号【{n}】：{d}（+{rwd} 灵石）")

    # 奇遇仅在「带截图(已验证)的录入」后按概率触发
    if args.proof:
        q = roll_quest(
            data, f"{rec['date']}|{scene}|{actual}|{rec['timestamp']}", rec_date)
        if q:
            save(data)
            print(f"\n🎲 奇遇降临【{q['name']}】！")
            print(f"   试炼：{q['desc']}")
            print(f"   限期：{q['days']} 日内（截止 {q['due_date']}）"
                  f"完成并带截图录入，达成即赏 +{q['reward']} 灵石。")
            print(f"   （完成后运行：quest claim --proof <截图>；查看：quest）")


def cmd_monthly(args):
    """设定/查看月度 bonus 预算（旅游/宠物等非每日开支）。"""
    data = load()
    require(data)
    aff = affinity_category(data)
    if args.show or not args.items:
        if not data["monthly_budgets"]:
            print("尚未设定月度 bonus 预算。示例：monthly 旅游=500:玩乐 宠物=300 医疗=200")
            print(f"格式 项目=月额[:品类]；品类可省略(自动推断)。可选品类: {'/'.join(CATEGORIES)}")
            print("（月度预算按月结算：跨月后自动把上月省下的折算成修为）")
            return
        total = sum(b["amount"] for b in data["monthly_budgets"].values())
        print("🗺️ 月度 bonus 预算（秘境额度）:")
        for scene, b in data["monthly_budgets"].items():
            star = " ★亲和" if aff and b["category"] == aff else ""
            print(f"  · {scene}: {fmt_money(b['amount'])} 元/月 [{b['category']}]{star}")
        print(f"  ── 月度预算合计: {fmt_money(total)} 元/月")
        return
    for item in args.items:
        if "=" not in item:
            print(f"忽略无法解析的项: {item}（应为 项目=月额[:品类]）")
            continue
        scene, rest = item.split("=", 1)
        scene = scene.strip()
        cat = None
        if ":" in rest:
            amt_s, cat = rest.split(":", 1)
            cat = cat.strip()
            if cat not in CATEGORIES:
                print(f"  ⚠️ 品类 '{cat}' 非法，自动推断。可选: {'/'.join(CATEGORIES)}")
                cat = None
        else:
            amt_s = rest
        try:
            amt = float(amt_s)
        except ValueError:
            print(f"忽略无法解析的金额: {item}")
            continue
        if cat is None:
            cat = infer_category(scene)
        data["monthly_budgets"][scene] = {"amount": amt, "category": cat}
    save(data)
    total = sum(b["amount"] for b in data["monthly_budgets"].values())
    print("✅ 月度 bonus 预算已更新（秘境额度）:")
    for scene, b in data["monthly_budgets"].items():
        star = " ★亲和" if aff and b["category"] == aff else ""
        print(f"  · {scene}: {fmt_money(b['amount'])} 元/月 [{b['category']}]{star}")
    print(f"  ── 月度预算合计: {fmt_money(total)} 元/月")


def cmd_spend(args):
    """录入一笔月度 bonus 项的开支（累计入当月，不立即结算）。"""
    data = load()
    require(data)
    scene = args.scene.strip()
    if scene not in data["monthly_budgets"]:
        print(f"⚠️ 项目【{scene}】不在月度预算表里。已有: "
              f"{', '.join(data['monthly_budgets'].keys()) or '（空）'}")
        if scene in data["budgets"]:
            print(f"提示：【{scene}】是每日预算项，请用 record 命令录入。")
        else:
            print("可先用 monthly 把它加进月度预算，或检查名称。")
        sys.exit(1)

    # 先结算历史月份
    settles = settle_past_months(data)
    _print_settlements(settles)

    category = data["monthly_budgets"][scene]["category"]
    amount = float(args.amount)
    month = args.month or this_month()
    entry = {"month": month, "scene": scene, "category": category,
             "amount": amount, "proof": args.proof or "",
             "verified": bool(args.proof), "note": args.note or "",
             "timestamp": now_str()}
    data["monthly_spend"].append(entry)
    save(data)

    mp = month_progress(data, month)[scene]
    print(f"🗺️ 【{scene}·{category}】本月({month})记一笔花销 {fmt_money(amount)} 元。")
    over = mp["remaining"] < 0
    if over:
        print(f"   本月已花 {fmt_money(mp['spent'])} / 额度 {fmt_money(mp['budget'])} 元，"
              f"已超额 {fmt_money(-mp['remaining'])} 元！月底结算将反噬修为。")
    else:
        print(f"   本月已花 {fmt_money(mp['spent'])} / 额度 {fmt_money(mp['budget'])} 元，"
              f"剩余 {fmt_money(mp['remaining'])} 元。")
    print("   （月度项不立即结算，跨月后自动把省下的折算成修为）")


def cmd_status(args):
    data = load()
    require(data)
    settles = settle_past_months(data)
    day = args.date or today_str()
    expired = expire_quests(data, day)
    if settles or expired:
        save(data)
        _print_settlements(settles)
    if expired:
        print(f"⌛ 奇遇【{expired['name']}】已过期（截止 {expired['due_date']}），奖励落空。")
    stats = compute_stats(data)
    xp = stats["xp"]
    r = realm_of(xp)
    stones = spirit_stones(data, stats)
    todays = records_on(data, day)
    ach_list = eval_achievements(stats)
    unlocked = sum(1 for (_, _, _, ok) in ach_list if ok)
    aff = stats["affinity"]

    root_line = "（未择灵根）"
    if data.get("root"):
        nm, motto, a = ROOTS[data["root"]]
        root_line = f"{nm} — {motto} 亲和【{a}】×{BONUS}"

    cs = stats["current_streak"]
    dx_name, dx_coef = daoxin_by_streak(cs)
    if cs >= 1:
        daoxin = f"{dx_name}（连修 {cs} 天，下一笔灵石吸收 ×{dx_coef}）"
    else:
        daoxin = f"{dx_name}（已断更，下一笔吸收 ×{dx_coef}；连修即回升，续上即可恢复）"

    xp_line = f"修为:    {fmt_money(xp)} 点"
    parts = []
    if abs(stats.get("aff_extra", 0)) > 1e-9:
        parts.append(f"灵根亲和 +{fmt_money(stats['aff_extra'])}")
    dxe = stats.get("daoxin_extra", 0)
    if abs(dxe) > 1e-9:
        parts.append(f"道心 {'+' if dxe >= 0 else ''}{fmt_money(dxe)}")
    if parts:
        xp_line += "（含 " + "、".join(parts) + " 点）"

    dtitle = display_title(data, stats, xp)
    if not has_started(data):
        realm_line = f"【{dtitle}】 (尚未踏入仙途，首笔记录即破境入练气期)"
    else:
        realm_line = f"【{dtitle}】 (第 {r['idx']+1}/{len(REALMS)} 重天)"
    print("══════════════ 修行档案 ══════════════")
    print(f"灵根:    {root_line}")
    print(f"境界:    {realm_line}")
    print(xp_line)
    print(f"实际攒下: {fmt_money(stats['net_real'])} 元  "
          f"(累计省 {fmt_money(stats['total_saved'])} / 超支 {fmt_money(stats['total_loss'])})")
    print(f"进度:    {progress_bar(xp)}")
    print(f"灵石:    {stones} 枚  ·  道号 {unlocked}/{len(ach_list)} 个")
    print(f"道心:    {daoxin}（最长连修 {stats['max_streak']} 天）")
    print(f"修炼:    共 {stats['cultivation_days']} 天 · {stats['total_records']} 次记录")
    print("──────────────────────────────────────")

    budgets = data["budgets"]
    if not budgets:
        print("（尚未设定预算，请先 budget）")
    else:
        recorded = {x["scene"] for x in todays}
        print(f"今日 {day}:")
        day_saved = sum(x["saved"] for x in todays)
        for scene, b in budgets.items():
            star = "★" if aff and b["category"] == aff else " "
            if scene in recorded:
                rr = [x for x in todays if x["scene"] == scene][-1]
                tag = "✓" if rr["verified"] else "·"
                sd = rr["saved"]
                mark = f"省{fmt_money(sd)}" if sd >= 0 else f"超{fmt_money(-sd)}"
                print(f"  {tag}{star}{scene}[{b['category']}]: 实花 {fmt_money(rr['actual'])} ({mark})")
            else:
                print(f"  ○{star}{scene}[{b['category']}]: 待录入 (预算 {fmt_money(b['amount'])})")
        sign = "省下" if day_saved >= 0 else "超支"
        print(f"  ── 今日净{sign}: {fmt_money(abs(day_saved))} 元")

    if data["monthly_budgets"]:
        print("──────────────────────────────────────")
        cur = this_month()
        mp = month_progress(data, cur)
        print(f"本月 bonus 预算 {cur}（月底自动结算）:")
        for scene, info in mp.items():
            star = "★" if aff and info["category"] == aff else " "
            rem = info["remaining"]
            if not info["recorded"]:
                print(f"  ○{star}{scene}[{info['category']}]: 月额 {fmt_money(info['budget'])} 元（本月未录入）")
            elif rem >= 0:
                print(f"  ·{star}{scene}[{info['category']}]: 已花 {fmt_money(info['spent'])}/"
                      f"{fmt_money(info['budget'])} 元，剩 {fmt_money(rem)}")
            else:
                print(f"  ⚔{star}{scene}[{info['category']}]: 已花 {fmt_money(info['spent'])}/"
                      f"{fmt_money(info['budget'])} 元，超 {fmt_money(-rem)}")

    q = data["quests"].get("active")
    if q:
        print("──────────────────────────────────────")
        left = (date.fromisoformat(q["due_date"]) - date.fromisoformat(day)).days
        left_s = f"剩 {left} 日" if left > 0 else ("今日截止" if left == 0 else "已逾期")
        print(f"🎲 在途奇遇【{q['name']}】[{q['category']}]（{left_s}，截止 {q['due_date']}）:")
        print(f"   {q['desc']}")
        print(f"   达成赏 +{q['reward']} 灵石 · 完成后 quest claim --proof <截图>")
    print("══════════════════════════════════════")


def cmd_achievements(args):
    data = load()
    require(data)
    stats = compute_stats(data)
    ach_list = eval_achievements(stats)
    unlocked = sum(1 for (_, _, _, ok) in ach_list if ok)
    print(f"🏅 道号录 ({unlocked}/{len(ach_list)} 已得):")
    for (n, d, rwd, ok) in ach_list:
        mark = "✅" if ok else "🔒"
        print(f"  {mark} 【{n}】 {d}  (+{rwd}灵石)")
    print(f"灵石总计: {spirit_stones(data, stats)} 枚")


def cmd_ledger(args):
    data = load()
    require(data)
    recs = data["records"][-args.n:]
    if not recs:
        print("（暂无记录）")
        return
    print(f"📖 最近 {len(recs)} 条记录:")
    for r in recs:
        sd = r["saved"]
        mark = f"省 {fmt_money(sd)}" if sd >= 0 else f"超 {fmt_money(-sd)}"
        v = "✓验" if r["verified"] else "未验"
        cat = record_category(data, r)
        note = f" — {r['note']}" if r["note"] else ""
        print(f"  {r['date']} {r['scene']}[{cat}]: 预算{fmt_money(r['planned'])}/实花{fmt_money(r['actual'])} "
              f"[{mark}|{v}]{note}")


def cmd_quest(args):
    """查看在途奇遇 / 达成领奖。无子动作=查看；claim=达成结算奖励灵石。"""
    data = load()
    require(data)
    today = args.date or today_str()
    expired = expire_quests(data, today)
    if expired:
        save(data)
        print(f"⌛ 奇遇【{expired['name']}】已过期（截止 {expired['due_date']}），奖励落空。")

    if args.action == "claim":
        q = data["quests"].get("active")
        if not q:
            print("当前无在途奇遇可领。带截图录入花销时有概率触发新奇遇。")
            return
        if not args.proof:
            print("领赏须凭证：达成后用 quest claim --proof <截图路径>。"
                  "（俭道重诚，无凭不予结算）")
            return
        q = dict(q)
        q["outcome"] = "claimed"
        q["closed_date"] = today
        q["proof"] = args.proof
        data["quests"]["active"] = None
        data["quests"]["log"].append(q)
        save(data)
        stats = compute_stats(data)
        print(f"🎉 奇遇【{q['name']}】达成！+{q['reward']} 灵石入账。")
        print(f"   灵石总计: {spirit_stones(data, stats)} 枚")
        return

    # 查看
    q = data["quests"].get("active")
    if q:
        left = (date.fromisoformat(q["due_date"]) - date.fromisoformat(today)).days
        left_s = f"剩 {left} 日" if left > 0 else ("今日截止" if left == 0 else "已逾期")
        print(f"🎲 在途奇遇【{q['name']}】[{q['category']}]（{left_s}，截止 {q['due_date']}）")
        print(f"   试炼：{q['desc']}")
        print(f"   达成赏 +{q['reward']} 灵石 · 完成后：quest claim --proof <截图>")
    else:
        print("当前无在途奇遇。带截图录入花销时，有概率触发限时省钱奇遇。")
    log = data["quests"].get("log", [])
    if log:
        done = sum(1 for e in log if e.get("outcome") == "claimed")
        miss = sum(1 for e in log if e.get("outcome") == "expired")
        print(f"\n📜 奇遇录：达成 {done} · 错失 {miss}"
              f"（累计奇遇灵石 +{quest_reward_total(data)} 枚）")
        for e in log[-args.n:]:
            tag = "✅" if e.get("outcome") == "claimed" else "⌛"
            rwd = f"+{e['reward']}" if e.get("outcome") == "claimed" else "0"
            print(f"  {tag} {e['name']}（{rwd} 灵石）")


def cmd_roots(args):
    print(f"🌟 五行灵根（init --root 选择）—— 亲和品类省下的钱修为 ×{BONUS}:")
    for k, (nm, motto, aff) in ROOTS.items():
        print(f"  [{k}] {nm} · 亲和【{aff}】 — {motto}")
    print(f"\n品类共 {len(CATEGORIES)} 类: {'/'.join(CATEGORIES)}")


def main():
    p = argparse.ArgumentParser(description="攒钱修仙引擎 v4")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="开辟道场(初始化)")
    pi.add_argument("--root", default="", help="五行灵根: 金/木/水/火/土")
    pi.add_argument("--force", action="store_true", help="重置已有数据")
    pi.set_defaults(func=cmd_init)

    pb = sub.add_parser("budget", help="设定/查看每日预算")
    pb.add_argument("items", nargs="*", help="场景=金额[:品类]，如 午餐=30:饮食")
    pb.add_argument("--show", action="store_true", help="仅查看")
    pb.set_defaults(func=cmd_budget)

    pr = sub.add_parser("record", help="录入一笔每日实际花销")
    pr.add_argument("scene", help="场景名(须在每日预算表中)")
    pr.add_argument("actual", help="实际花销金额")
    pr.add_argument("--proof", default="", help="支付截图路径(已验证)")
    pr.add_argument("--note", default="", help="备注")
    pr.add_argument("--date", default="", help="日期 YYYY-MM-DD，默认今天")
    pr.set_defaults(func=cmd_record)

    pm = sub.add_parser("monthly", help="设定/查看月度 bonus 预算(旅游/宠物等)")
    pm.add_argument("items", nargs="*", help="项目=月额[:品类]，如 旅游=500:玩乐")
    pm.add_argument("--show", action="store_true", help="仅查看")
    pm.set_defaults(func=cmd_monthly)

    psp = sub.add_parser("spend", help="录入一笔月度 bonus 开支")
    psp.add_argument("scene", help="项目名(须在月度预算表中)")
    psp.add_argument("amount", help="本次花销金额")
    psp.add_argument("--proof", default="", help="支付截图路径(已验证)")
    psp.add_argument("--note", default="", help="备注")
    psp.add_argument("--month", default="", help="月份 YYYY-MM，默认本月")
    psp.set_defaults(func=cmd_spend)

    ps = sub.add_parser("status", help="查看修行档案")
    ps.add_argument("--date", default="", help="查看某天，默认今天")
    ps.set_defaults(func=cmd_status)

    pa = sub.add_parser("achievements", help="查看道号(成就)录")
    pa.set_defaults(func=cmd_achievements)

    pl = sub.add_parser("ledger", help="查看历史记录")
    pl.add_argument("-n", type=int, default=15, help="显示条数")
    pl.set_defaults(func=cmd_ledger)

    pt = sub.add_parser("roots", help="查看五行灵根说明")
    pt.set_defaults(func=cmd_roots)

    pq = sub.add_parser("quest", help="查看在途奇遇 / 达成领奖")
    pq.add_argument("action", nargs="?", default="show",
                    choices=["show", "claim"], help="show=查看(默认), claim=达成领奖")
    pq.add_argument("--proof", default="", help="达成凭证截图路径(claim 时必带)")
    pq.add_argument("--date", default="", help="日期 YYYY-MM-DD，默认今天")
    pq.add_argument("-n", type=int, default=8, help="奇遇录显示条数")
    pq.set_defaults(func=cmd_quest)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
