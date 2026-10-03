"""测试用的确定性「嵌入」：概念词袋。

它不是语义模型 —— 只是让「同一概念、零字面重叠」的两句话向量相近，用来证明语义这一路
从写入、遗忘、还原到召回融合的**管道**是通的。召回质量必须用真实模型在真实数据上测。

用法：NYX_EMBED_PROVIDER=tests.fixtures.fake_embed:provider
"""
import math

CONCEPTS = [
    ("pet", ("cat", "kitten", "dog", "puppy", "beagle", "pet", "猫", "狗", "宠物")),
    ("employment", ("company", "employer", "work", "job", "joined", "engineer",
                    "公司", "上班", "入职", "跳槽", "工程师", "单位")),
    ("reading", ("book", "read", "novel", "书", "读", "《")),
    ("cooking", ("recipe", "bake", "oven", "banana", "番茄", "做法", "调料", "炒")),
    ("allergy", ("allerg", "peanut", "过敏", "花生", "芒果")),
    ("travel", ("trip", "travel", "seattle", "pack", "成都", "旅行", "玩")),
    ("running", ("marathon", "ran ", "running", "跑", "马拉松")),
    ("tea", ("tea", "茶")),
    ("payment", ("payment", "account", "付款", "账户", "转账")),
]


class ConceptBag:
    name = "fake:concept-bag-v1"
    ready = True

    def encode(self, texts):
        out = []
        for t in texts:
            low = (t or "").lower()
            v = [float(sum(low.count(w) for w in words)) for _, words in CONCEPTS] + [0.05]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class Renamed(ConceptBag):
    name = "fake:concept-bag-v2"


class NotReady(ConceptBag):
    name = "fake:loading"
    ready = False

    def encode(self, texts):
        raise AssertionError("召回路径不该在模型就绪前嵌入")


def provider():
    return ConceptBag()
