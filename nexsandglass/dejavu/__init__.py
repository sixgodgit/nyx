"""Déjà Vu —— 把「检索失败」当作一类信号来建模。

存储层零外部依赖（stdlib + sqlite3）。

    from nexsandglass.dejavu import DejaVu

    dv = DejaVu("./my_memory")
    dv.imprint("note:1", "上周三和张老板聊了川菜馆的事")
    r = dv.sense("我们聊过的那家川菜馆")
    if r.familiar and not r.score == 0:
        for p in dv.hunt("川菜馆"):
            print(p.snippet)
"""

from .core import DejaVu
from .types import FamiliarityResult, GazeReport, Phantom

__all__ = ["DejaVu", "FamiliarityResult", "Phantom", "GazeReport"]
__version__ = "1.0.0"
