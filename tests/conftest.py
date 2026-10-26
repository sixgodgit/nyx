"""测试会话的隔离（v7.13.9 数据目录；v7.14.0 外部服务与可选后端）。

pytest 先 import 这个文件，再 import 任何测试模块 —— 所以这里设的环境变量，在 nexsandglass 各模块
import 时把路径定死之前就已生效。**无条件覆盖**：2026-09-13 事故现场 NEXSANDBASE_HOME 就是设了、
就指着生产库，测试把 107 条合成记忆写进了生产中枢。v7.13.8 时复现：不设它直接跑测试，
真实数据目录的日志里多出「e2e | 用户喜欢手冲咖啡」。

同样的道理也适用于开发者环境里的其它配置（v7.14.0）：
  - 设了 EMBEDDING_API_URL / NYX_EMBED_PROVIDER 的话，测试数据会被发给外部的嵌入服务
  - 装了 [vector]（sentence-transformers）的话，语义 auto 会在后台用真模型补向量，后面的测试等它等到超时
    （实测：13 项报错「后台补索引线程没有结束」，CI 不装这个依赖所以看不到）
所以语义默认关；要测语义的测试自己注入假模型（tests/fixtures/fake_embed.py）。

第二道防线在 nexsandglass/core/prodguard.py：测试进程碰临时目录以外的库直接报错。
"""
import os
import tempfile

_SESSION = tempfile.mkdtemp(prefix="nyx-pytest-")
os.environ["NEXSANDBASE_HOME"] = os.path.join(_SESSION, "nb")
os.environ["HERMES_HOME"] = os.path.join(_SESSION, "hermes")
os.environ.pop("NYX_ALLOW_PROD_DB", None)

os.environ["NYX_EMBED"] = "off"
for _k in ("NYX_EMBED_PROVIDER", "NYX_EMBED_MODEL", "EMBEDDING_API_URL", "EMBEDDING_API_KEY", "NYX_RERANK",
           "NYX_SECRET_FILTER", "NYX_UNDERSTAND_LLM", "WTHREAD_LLM_EXTRACTION", "LLM_EXTRACT_API_URL",
           "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "NYX_EVAL_API_URL", "NYX_EVAL_API_KEY", "NYX_JUDGE_API_URL",
           "NYX_JUDGE_API_KEY", "NYX_VAULT", "NYX_VAULT_BACKEND", "NYX_VAULT_REVEAL", "NYX_VAULT_KINDS",
           "NYX_VAULT_KEY", "HERMES_CRON_SESSION"):
    os.environ.pop(_k, None)
# 保险箱的主密钥在 $XDG_CONFIG_HOME/nyx/vault.key（v7.15.0）：不碰开发者真实的密钥文件
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SESSION, "config")
# 给人看的输出默认英文，中文系统语言下才是中文（v7.16.0）：测试不随开发者的系统语言变；要测中文的自己设 zh
os.environ["NYX_LANG"] = "en"
