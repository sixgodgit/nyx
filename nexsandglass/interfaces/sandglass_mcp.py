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
from nexsandglass.core import hints


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
            from nexsandglass.core import semantic
            # v7.13.3：ping 以前恒报 ok。语义这一路静默失效一个月，ping 每次都是绿的
            sem = semantic.health_check()
            status = "ok" if sem.get("ok") else "degraded"
            return _result(request_id, {
                "content": [{
                    "type": "text",
                    "text": json.dumps({"status": status, "sands": count(), "stage": _current_stage(),
                                        "semantic_index": {"state": sem.get("state"), "problems": sem.get("problems"),
                                                           "indexed": sem.get("indexed"), "pending": sem.get("pending")},
                                        "full_check": hints.cmd("doctor")},
                                       ensure_ascii=False, default=str)
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
            r = entropy_ghost(args.get("question", "如果选另一个选项"))   # 默认问题给中文分析器用，不是界面文字
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
    _tool("sandglass_ping", "Health check: number of memories, current stage, semantic index status "
                            "(status=degraded means one retrieval path is down)."),
    _tool("sandglass_search", "Keyword search over the user's memories.",
          {"query": {"type": "string", "description": "What to search for"},
           "limit": {"type": "integer", "description": "Number of results"}},
          ["query"]),
    _tool("sandglass_semantic", "Fuzzy search over memories (synonyms + SimHash + TF-IDF).",
          {"query": {"type": "string", "description": "What to search for"},
           "limit": {"type": "integer", "description": "Number of results"},
           "backend": {"type": "string", "description": "tfidf or chromadb"}},
          ["query"]),
    _tool("sandglass_recent", "The most recent N memories.",
          {"limit": {"type": "integer", "description": "Number of results"}}),
    _tool("sandglass_offset", "The user's decision drift (saving / spending / drifting)."),
    _tool("sandglass_persona", "The user's profile for the current stage."),
    _tool("sandglass_tasks", "Open to-do items."),
    _tool("sandglass_echo", "Recent sentiment trend."),
    _tool("sandglass_dream", "Ghost decision: what if the user had picked the other option?",
          {"question": {"type": "string", "description": "The decision in question"}}),
    _tool("sandglass_chart", "ASCII chart of emotional entropy.",
          {"n": {"type": "integer", "description": "How many entries to sample"}}),
    _tool("sandglass_migrate", "Export all memory data as a tar.gz.",
          {"output": {"type": "string", "description": "Output path"}}),
    _tool("sandglass_soul_export", "Export the soul diff (decision drift + decisions + sentiment).",
          {"output": {"type": "string", "description": "Output path"}}),
    _tool("sandglass_soul_merge", "Merge an external soul diff.",
          {"source": {"type": "string", "description": "Path of the soul diff file"}},
          ["source"]),
    _tool("sandglass_import", "Import another Nyx journal, or a ChatGPT / Claude conversation export.",
          {"source_path": {"type": "string", "description": "Source file path"},
           "format": {"type": "string", "description": "sandglass/chatgpt/claude/plain"}},
          ["source_path"]),
    _tool("sandglass_export", "Export the journal to a portable file.",
          {"output_path": {"type": "string", "description": "Output path"},
           "limit": {"type": "integer", "description": "Maximum number of entries"},
           "month": {"type": "string", "description": "Only this month, yyyy-mm"}}),
    _tool("sandglass_thread", "Query the knowledge graph: entity–relation triples.",
          {"entity": {"type": "string", "description": "Entity"},
           "relation": {"type": "string", "description": "Relation"},
           "limit": {"type": "integer", "description": "Number of results"}}),
    _tool("sandglass_thread_graph", "Subgraph around an entity, N hops out.",
          {"entity": {"type": "string", "description": "Center entity"},
           "depth": {"type": "integer", "description": "How many hops"}},
          ["entity"]),
    _tool("sandglass_thread_weave", "Summary of cause-and-effect chains in the knowledge graph.",
          {"limit": {"type": "integer", "description": "Number of results"}}),
    _tool("sandglass_thread_add", "Add a triple by hand, when the agent notices one was missed.",
          {"subject": {"type": "string", "description": "Subject"},
           "relation": {"type": "string", "description": "Relation"},
           "object": {"type": "string", "description": "Object"}},
          ["subject", "relation", "object"]),
    _tool("fact_store", "Structured fact store with trust scores: action=add/search/probe.",
          {"action": {"type": "string", "description": "add/search/probe"},
           "content": {"type": "string", "description": "The fact to store"},
           "category": {"type": "string", "description": "Category"},
           "query": {"type": "string", "description": "Search terms"},
           "entity": {"type": "string", "description": "Entity"}},
          ["action"]),
    _tool("fact_feedback", "Mark a memory as helpful or not (adjusts its trust score).",
          {"line_num": {"type": "integer", "description": "Journal line number"},
           "helpful": {"type": "boolean", "description": "Was it helpful"}},
          ["line_num", "helpful"]),
    _tool("sandglass_dejavu", "Déjà Vu: tell \"never discussed\" apart from \"discussed but not found\" (check/stats/hunt).",
          {"action": {"type": "string", "description": "check/stats/hunt"},
           "query": {"type": "string", "description": "What to check"}}),
    _tool("memory_observe", "Write a memory: observe an event and promote it to long-term memory if it is worth keeping.",
          {"content": {"type": "string", "description": "What happened"},
           "source": {"type": "string", "description": "Source (mcp/user/assistant)"}},
          ["content"]),
    _tool("memory_recall", "Recall memories relevant to a query (intent-aware).",
          {"query": {"type": "string", "description": "Query"},
           "budget": {"type": "integer", "description": "Token budget"},
           "limit": {"type": "integer", "description": "Number of results"}},
          ["query"]),
    _tool("memory_feedback", "Mark recalled memories as helpful or not.",
          {"memory_ids": {"type": "array", "items": {"type": "string"},
                          "description": "Ids of the recalled memories"},
           "helpful": {"type": "boolean", "description": "Were they helpful"}}),
    _tool("memory_forget",
          "Forget memories. By default they move to a quarantine (restorable until the retention period ends, then "
          "erased). Call with dry_run=true first to see what would be forgotten. ok=false means nothing was forgotten.",
          {"memory_id": {"type": "string", "description": "Memory id (the id in recall results, or an m_… mem_id)"},
           "source_id": {"type": "string", "description": "Source id (older form)"},
           "contains": {"type": "string", "description": "Exact substring of the memory text"},
           "dry_run": {"type": "boolean", "description": "Preview only, forget nothing"},
           "all": {"type": "boolean", "description": "Forget everything (careful; also goes to the quarantine first)"}}),
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
