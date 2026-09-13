"""Déjà Vu 最小示例 —— 30 行，零配置，零 API key。

    pip install nyx-memory
    python examples/dejavu_minimal.py

演示完整闭环：写入 → 感知熟悉 → 寻回痕迹。
"""

from nexsandglass.dejavu import DejaVu

# 1. 指向任意目录即可，不需要任何配置
dv = DejaVu("./my_memory")

# 2. 写入两条记忆（key 是你自己的引用，行号 / 消息 ID / URL 都行）
dv.imprint("msg-1", "上周三和张老板聊了川菜馆的事，他说要换厨师")
dv.imprint("msg-2", "今天下午三点开季度复盘会")

# 3. 检索失败时，先问一句「我是不是接触过？」
r = dv.sense("我们聊过的那家川菜馆")
print(f"熟悉度 {r.score:.2f} | {r.reason}")

# 4. 熟悉的话，把痕迹钓出来
if r.familiar:
    for p in dv.hunt("川菜馆 张老板"):
        print(f"  · {p.token} 出现 {p.sightings} 次 — 「{p.snippet}」")

# 5. 对比：一个从没提过的话题
print(dv.sense("量子计算对密码学的影响").reason)

dv.close()   # 或 with DejaVu(...) as dv:
