"""
NexSandglass MCP Server V2.6.15
===============================
标准 MCP 2024-11-05 协议——任何 MCP 兼容 Agent 可直接调用。
启动: python nexsandglass/interfaces/sandglass_mcp.py

修复 V2.6.14 中的问题：
- tools/call 返回格式严格符合 MCP 标准（去掉 structuredContent 非标准字段）
- 增强异常隔离，防止单个工具调用异常导致连接断开
- 移除进程启动时的 stdout 输出
"""

import sys
import os
import json
import traceback

# 让脚本可在仓库内直接运行
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 将 stderr 重定向到 /dev/null，避免任何模块警告破坏 stdio MCP 交互
# 注意：这会导致错误日志丢失，如需调试可以在本地用 env DEBUG_NYX=1 禁用重定向
if not os.environ.get("DEBUG_NYX"):
    try:
        _devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(_devnull, 2)
        os.close(_devnull)
    except Exception:
        pass

from nexsandglass.core.sandglass_paths import __version__


def _send(obj):
    """发送 JSON-RPC 消息并立即刷新 stdout。"""
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _result(request_id, payload):
    """标准 MCP tools/call 返回: result 字段必须是 {content: [...]}。"""
    if isinstance(payload, dict) and "content" in payload and isinstance(payload["content"], list):
        _send({"jsonrpc": "2.0", "id": request_id, "result": payload})
    else:
        _send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
                ]
            }
        })


def _error(request_id, code, message):
    """返回 JSON-RPC 错误并同时包装成 MCP tools/call 的错误格式。"""
    _send({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps({"error": message}, ensure_ascii=False)}],
            "isError": True,
        },
        "error": {"code": code, "message": message},
    })


def _handle_tool(name, args, request_id):
    try:
        if name == "sandglass_ping":
            from nexsandglass.features.sandglass_vault import count
            from nexsandglass.features.sandglass_think import _current_stage
            return _result(request_id, {
                "content": [{
                    "type": "text",
                    "text": json.dumps({"status": "ok", "sands": count(), "stage": _current_stage()}, ensure_ascii=False)
                }]
            })

        elif name == "sandglass_search":
            from nexsandglass.runtime.orchestrator import get_orchestrator
            orch = get_orchestrator()
            rr = orch.recall(args.get("query", ""), token_budget=args.get("limit", 10) * 200)
            texts = rr.strings[: args.get("limit", 10)]
            data = [
                {"text": t[:200], "id": rr.memory_ids[i] if i < len(rr.memory_ids) else t}
                for i, t in enumerate(texts)
            ]
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]})

        elif name == "sandglass_semantic":
            from nexsandglass.features.sandglass_think import search_semantic
            backend = args.get("backend", "tfidf")
            r = search_semantic(args.get("query", ""), limit=args.get("limit", 5), backend=backend)
            data = [{"line": ln, "ts": ts, "text": txt[:200]} for ln, ts, txt, *_ in r]
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]})

        elif name == "sandglass_recent":
            from nexsandglass.features.sandglass_vault import recent
            r = recent(args.get("limit", 10))
            data = [{"line": ln, "ts": ts, "text": txt[:200]} for ln, ts, txt, *_ in r]
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]})

        elif name == "sandglass_offset":
            from nexsandglass.features.sandglass_think import comprehensive_offset
            r = comprehensive_offset()
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}]})

        elif name == "sandglass_persona":
            from nexsandglass.features.sandglass_think import _current_stage
            from nexsandglass.l3.persona_l3 import _local_persona_extract
            p = _local_persona_extract()
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"stage": _current_stage(), "persona": p[:500]}, ensure_ascii=False)}]})

        elif name == "sandglass_tasks":
            from nexsandglass.l3.l3_tasks import task_pending
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(task_pending(), ensure_ascii=False)}]})

        elif name == "sandglass_echo":
            from nexsandglass.l3.l3_search_core import _sentiment_wind
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"wind": _sentiment_wind()}, ensure_ascii=False)}]})

        elif name == "sandglass_dream":
            from nexsandglass.l3.emotion_l3 import entropy_ghost
            r = entropy_ghost(args.get("question", "如果选另一个选项"))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}]})

        elif name == "sandglass_chart":
            from nexsandglass.features.sandglass_think import entropy_chart
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"chart": entropy_chart(args.get("n", 10))}, ensure_ascii=False)}]})

        elif name == "sandglass_migrate":
            from nexsandglass.features.sandglass_think import memory_migrate
            path = memory_migrate(args.get("output", ""))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"exported": path}, ensure_ascii=False)}]})

        elif name == "sandglass_soul_export":
            from nexsandglass.features.soul_diff import export_soul
            path = export_soul(args.get("output", ""))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"soul": path}, ensure_ascii=False)}]})

        elif name == "sandglass_soul_merge":
            from nexsandglass.features.soul_diff import merge_soul
            n = merge_soul(args.get("source", ""))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"merged": n}, ensure_ascii=False)}]})

        elif name == "sandglass_import":
            from nexsandglass.features.sandglass_vault import sandglass_import
            r = sandglass_import(args.get("source_path", ""), args.get("format", "sandglass"))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}]})

        elif name == "sandglass_export":
            from nexsandglass.features.sandglass_vault import sandglass_export
            path = sandglass_export(args.get("output_path"), args.get("limit"), args.get("month", ""))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"exported": path}, ensure_ascii=False)}]})

        elif name == "sandglass_thread":
            from nexsandglass.features.weavethread import wthread_query
            r = wthread_query(args.get("entity"), args.get("relation"), args.get("limit", 20))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}]})

        elif name == "sandglass_thread_graph":
            from nexsandglass.features.weavethread import wthread_graph
            r = wthread_graph(args.get("entity", ""), args.get("depth", 1))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}]})

        elif name == "sandglass_thread_weave":
            from nexsandglass.features.weavethread import wthread_weave
            r = wthread_weave(args.get("limit", 3))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"causal_summary": r}, ensure_ascii=False)}]})

        elif name == "sandglass_thread_add":
            from nexsandglass.features.weavethread import wthread_add
            ok = wthread_add(args.get("subject", "user"), args.get("relation", ""), args.get("object", ""))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"added": ok}, ensure_ascii=False)}]})

        elif name == "fact_store":
            action = args.get("action", "search")
            if action == "add":
                from nexsandglass.runtime.orchestrator import get_orchestrator
                orch = get_orchestrator()
                fr = orch.observe(args.get("content", ""), source="fact_store")
                return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"status": "added" if fr.ok else "failed", "id": fr.memory_id}, ensure_ascii=False)}]})

            if action == "search":
                from nexsandglass.runtime.orchestrator import get_orchestrator
                orch = get_orchestrator()
                rr = orch.recall(args.get("query", ""), token_budget=2000)
                return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"results": [{"text": t[:200]} for t in rr.strings[:10]]}, ensure_ascii=False)}]})

            if action == "probe":
                from nexsandglass.features.shadow_sand import shadow_search as _ss
                shadow_hits = _ss(args.get("entity", ""), limit=20)
                return _result(request_id, {"content": [{"type": "text", "text": json.dumps([{"line": ln, "trust": score} for score, ln in shadow_hits], ensure_ascii=False)}]})

            return _error(request_id, -32601, f"Unknown fact_store action: {action}")

        elif name == "fact_feedback":
            from nexsandglass.features.shadow_sand import shadow_feedback
            result = shadow_feedback(args.get("line_num", 0), args.get("helpful", True))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]})

        elif name == "sandglass_dejavu":
            from nexsandglass.interfaces.nyx import nyx_gaze, nyx_hunt, nyx_sense
            action = args.get("action", "check")
            query = args.get("query", "")
            if action == "stats":
                return _result(request_id, {"content": [{"type": "text", "text": json.dumps(nyx_gaze(), ensure_ascii=False)}]})
            if action == "hunt":
                return _result(request_id, {"content": [{"type": "text", "text": json.dumps(nyx_hunt(query), ensure_ascii=False)}]})
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(nyx_sense(query), ensure_ascii=False)}]})

        elif name == "memory_observe":
            from nexsandglass.runtime.orchestrator import get_orchestrator
            orch = get_orchestrator()
            fr = orch.observe(args.get("content", ""), source=args.get("source", "mcp"))
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({"ok": fr.ok, "memory_id": fr.memory_id, "type": fr.memory_type, "lifecycle": fr.lifecycle_state}, ensure_ascii=False)}]})

        elif name == "memory_recall":
            from nexsandglass.runtime.orchestrator import get_orchestrator
            orch = get_orchestrator()
            mc = orch.recall(args.get("query", ""), token_budget=args.get("budget", 1500))
            mi = getattr(mc, "meta_intent", None)
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps({
                "results": [{"text": t[:300]} for t in mc.strings[: args.get("limit", 10)]],
                "strategy": getattr(mi, "strategy", None) if mi else None,
                "reasons": getattr(mi, "reasons", []) if mi else [],
            }, ensure_ascii=False)}]})

        elif name == "memory_feedback":
            from nexsandglass.runtime.facade import feedback
            result = feedback(args)
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]})

        elif name == "memory_forget":
            from nexsandglass.runtime.facade import forget
            result = forget(args)
            return _result(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]})

        else:
            return _error(request_id, -32601, f"Unknown tool: {name}")

    except Exception as e:
        tb = traceback.format_exc()
        return _error(request_id, -32000, f"{e}\n{tb}")


def _tool(name, description, properties=None, required=None):
    """构造 MCP 工具声明（含 inputSchema，符合协议要求）。"""
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


TOOLS = [
    _tool("sandglass_ping", "健康检查——返回沙漏总数和当前阶段"),
    _tool("sandglass_search", "关键词搜索记忆",
          {"query": {"type": "string", "description": "搜索关键词"},
           "limit": {"type": "integer", "description": "返回条数"}},
          ["query"]),
    _tool("sandglass_semantic", "语义搜索记忆(同义词+SimHash+TF-IDF)",
          {"query": {"type": "string", "description": "搜索内容"},
           "limit": {"type": "integer", "description": "返回条数"},
           "backend": {"type": "string", "description": "tfidf 或 chromadb"}},
          ["query"]),
    _tool("sandglass_recent", "最近N条记忆",
          {"limit": {"type": "integer", "description": "返回条数"}}),
    _tool("sandglass_offset", "当前偏移率(省钱/愿投/放弃)"),
    _tool("sandglass_persona", "当前阶段画像"),
    _tool("sandglass_tasks", "待办事项列表"),
    _tool("sandglass_echo", "当前回音折风向"),
    _tool("sandglass_dream", "幽灵决策——'如果选另一个选项会怎样'",
          {"question": {"type": "string", "description": "决策问题"}}),
    _tool("sandglass_chart", "情绪熵 ASCII 可视化图表",
          {"n": {"type": "integer", "description": "取样条数"}}),
    _tool("sandglass_migrate", "一键导出全部记忆数据为 tar.gz",
          {"output": {"type": "string", "description": "输出路径"}}),
    _tool("sandglass_soul_export", "导出灵魂差分(偏移率+决策+回音折)",
          {"output": {"type": "string", "description": "输出路径"}}),
    _tool("sandglass_soul_merge", "合并外部灵魂差分",
          {"source": {"type": "string", "description": "灵魂差分文件路径"}},
          ["source"]),
    _tool("sandglass_import", "导入外部沙漏或ChatGPT/Claude对话导出",
          {"source_path": {"type": "string", "description": "源文件路径"},
           "format": {"type": "string", "description": "sandglass/chatgpt/claude/plain"}},
          ["source_path"]),
    _tool("sandglass_export", "导出沙漏为可迁移文件",
          {"output_path": {"type": "string", "description": "输出路径"},
           "limit": {"type": "integer", "description": "条数上限"},
           "month": {"type": "string", "description": "月份过滤 yyyy-mm"}}),
    _tool("sandglass_thread", "查询织线知识图谱——实体关系三元组",
          {"entity": {"type": "string", "description": "实体"},
           "relation": {"type": "string", "description": "关系"},
           "limit": {"type": "integer", "description": "返回条数"}}),
    _tool("sandglass_thread_graph", "织线实体子图——展开N跳关系",
          {"entity": {"type": "string", "description": "中心实体"},
           "depth": {"type": "integer", "description": "展开深度"}},
          ["entity"]),
    _tool("sandglass_thread_weave", "织线→织布机桥接——因果链摘要",
          {"limit": {"type": "integer", "description": "返回条数"}}),
    _tool("sandglass_thread_add", "手动补入三元组——Agent发现漏抓时调用",
          {"subject": {"type": "string", "description": "主语"},
           "relation": {"type": "string", "description": "关系"},
           "object": {"type": "string", "description": "宾语"}},
          ["subject", "relation", "object"]),
    _tool("fact_store", "影子沙事实存储——action=add/search/probe",
          {"action": {"type": "string", "description": "add/search/probe"},
           "content": {"type": "string", "description": "事实内容"},
           "category": {"type": "string", "description": "分类"},
           "query": {"type": "string", "description": "搜索词"},
           "entity": {"type": "string", "description": "实体"}},
          ["action"]),
    _tool("fact_feedback", "信任评分反馈——标记记忆是否有帮助",
          {"line_num": {"type": "integer", "description": "行号"},
           "helpful": {"type": "boolean", "description": "是否有帮助"}},
          ["line_num", "helpful"]),
    _tool("sandglass_dejavu", "Déjà Vu 模糊感知——check/stats/hunt",
          {"action": {"type": "string", "description": "check/stats/hunt"},
           "query": {"type": "string", "description": "查询内容"}}),
    _tool("memory_observe", "统一记忆写入入口——观察事件并晋升长期记忆",
          {"content": {"type": "string", "description": "事件内容"},
           "source": {"type": "string", "description": "来源(mcp/user/assistant)"}},
          ["content"]),
    _tool("memory_recall", "统一记忆召回入口——意图自适应召回相关记忆",
          {"query": {"type": "string", "description": "查询"},
           "budget": {"type": "integer", "description": "token 预算"},
           "limit": {"type": "integer", "description": "返回条数"}},
          ["query"]),
    _tool("memory_feedback", "统一反馈入口——标记召回结果是否有帮助",
          {"memory_ids": {"type": "array", "items": {"type": "string"},
                          "description": "召回记忆 id 列表"},
           "helpful": {"type": "boolean", "description": "是否有帮助"}}),
    _tool("memory_forget", "统一遗忘入口——按 selector 遗忘记忆",
          {"memory_id": {"type": "string", "description": "记忆 id"},
           "source_id": {"type": "string", "description": "来源 id"},
           "all": {"type": "boolean", "description": "清空全部(谨慎)"}}),
]


def main():
    """MCP stdio 主循环"""
    for line in sys.stdin:
        try:
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            method = req.get("method", "")

            # JSON-RPC 2.0: 无 id 的消息是 notification，不能回复
            if "id" not in req:
                continue
            tid = req["id"]

            if method == "initialize":
                _send({"jsonrpc": "2.0", "id": tid, "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "NexSandglass", "version": __version__}
                }})

            elif method == "tools/list":
                _send({"jsonrpc": "2.0", "id": tid, "result": {"tools": TOOLS}})

            elif method == "tools/call":
                name = req.get("params", {}).get("name", "")
                args = req.get("params", {}).get("arguments", {})
                _handle_tool(name, args, tid)

            else:
                _send({"jsonrpc": "2.0", "id": tid,
                       "error": {"code": -32601, "message": f"Unknown method: {method}"}})

        except json.JSONDecodeError:
            _send({"jsonrpc": "2.0", "id": 0,
                   "error": {"code": -32700, "message": "Parse error"}})
        except Exception as e:
            _send({"jsonrpc": "2.0", "id": 0,
                   "error": {"code": -32000, "message": str(e)}})


if __name__ == "__main__":
    main()
