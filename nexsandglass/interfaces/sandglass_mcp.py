"""
NexSandglass MCP Server V2.6.14
===============================
标准 MCP 协议——任何 MCP 兼容 Agent 可直接调用。
启动: python sandglass_mcp.py
"""

import sys
import os
import json

# 让脚本可在仓库内直接运行（包安装后无需此行）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from nexsandglass.core.sandglass_paths import __version__


def _rpc_response(id, result):
    return json.dumps({"jsonrpc": "2.0", "id": id, "result": result})


def _rpc_error(id, code, message):
    return json.dumps({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})


def _handle_tool(name, args, request_id):
    try:
        if name == "sandglass_ping":
            from nexsandglass.features.sandglass_vault import count
            from nexsandglass.features.sandglass_think import _current_stage
            return _rpc_response(request_id, {
                "status": "ok", "sands": count(), "stage": _current_stage()
            })

        elif name == "sandglass_search":
            from nexsandglass.features.sandglass_vault import search
            r = search(args.get("query", ""), limit=args.get("limit", 10))
            return _rpc_response(request_id, [
                {"line": ln, "ts": ts, "text": txt[:200]} for ln, ts, txt, *_ in r
            ])

        elif name == "sandglass_semantic":
            from nexsandglass.features.sandglass_think import search_semantic
            backend = args.get("backend", "tfidf")
            r = search_semantic(args.get("query", ""), limit=args.get("limit", 5),
                                backend=backend)
            return _rpc_response(request_id, [
                {"line": ln, "ts": ts, "text": txt[:200]} for ln, ts, txt, *_ in r
            ])

        elif name == "sandglass_recent":
            from nexsandglass.features.sandglass_vault import recent
            r = recent(args.get("limit", 10))
            return _rpc_response(request_id, [
                {"line": ln, "ts": ts, "text": txt[:200]} for ln, ts, txt, *_ in r
            ])

        elif name == "sandglass_offset":
            from nexsandglass.features.sandglass_think import comprehensive_offset
            r = comprehensive_offset()
            return _rpc_response(request_id, r)

        elif name == "sandglass_persona":
            from nexsandglass.features.sandglass_think import _current_stage
            from nexsandglass.l3.persona_l3 import _local_persona_extract
            p = _local_persona_extract()
            return _rpc_response(request_id, {"stage": _current_stage(), "persona": p[:500]})

        elif name == "sandglass_tasks":
            from nexsandglass.l3.l3_tasks import task_pending
            return _rpc_response(request_id, task_pending())

        elif name == "sandglass_echo":
            from nexsandglass.l3.l3_search_core import _sentiment_wind
            return _rpc_response(request_id, {"wind": _sentiment_wind()})

        elif name == "sandglass_dream":
            from nexsandglass.l3.emotion_l3 import entropy_ghost
            r = entropy_ghost(args.get("question", "如果选另一个选项"))
            return _rpc_response(request_id, r)

        elif name == "sandglass_chart":
            from nexsandglass.features.sandglass_think import entropy_chart
            return _rpc_response(request_id, {"chart": entropy_chart(args.get("n", 10))})

        elif name == "sandglass_migrate":
            from nexsandglass.features.sandglass_think import memory_migrate
            path = memory_migrate(args.get("output", ""))
            return _rpc_response(request_id, {"exported": path})

        elif name == "sandglass_soul_export":
            from nexsandglass.features.soul_diff import export_soul
            path = export_soul(args.get("output", ""))
            return _rpc_response(request_id, {"soul": path})

        elif name == "sandglass_soul_merge":
            from nexsandglass.features.soul_diff import merge_soul
            n = merge_soul(args.get("source", ""))
            return _rpc_response(request_id, {"merged": n})

        elif name == "sandglass_import":
            from nexsandglass.features.sandglass_vault import sandglass_import
            r = sandglass_import(args.get("source_path", ""), args.get("format", "sandglass"))
            return _rpc_response(request_id, r)

        elif name == "sandglass_export":
            from nexsandglass.features.sandglass_vault import sandglass_export
            path = sandglass_export(args.get("output_path"), args.get("limit"), args.get("month", ""))
            return _rpc_response(request_id, {"exported": path})

        elif name == "sandglass_thread":
            from nexsandglass.features.weavethread import wthread_query
            r = wthread_query(args.get("entity"), args.get("relation"), args.get("limit", 20))
            return _rpc_response(request_id, r)

        elif name == "sandglass_thread_graph":
            from nexsandglass.features.weavethread import wthread_graph
            r = wthread_graph(args.get("entity", ""), args.get("depth", 1))
            return _rpc_response(request_id, r)

        elif name == "sandglass_thread_weave":
            from nexsandglass.features.weavethread import wthread_weave
            r = wthread_weave(args.get("limit", 3))
            return _rpc_response(request_id, {"causal_summary": r})

        elif name == "sandglass_thread_add":
            from nexsandglass.features.weavethread import wthread_add
            ok = wthread_add(args.get("subject", "user"), args.get("relation", ""), args.get("object", ""))
            return _rpc_response(request_id, {"added": ok})

        elif name == "fact_store":
            action = args.get("action", "search")
            from nexsandglass.features.sandglass_vault import search as vs
            from nexsandglass.features.shadow_sand import shadow_search as _ss

            if action == "add":
                from nexsandglass.core.sandglass_log import log_message
                log_message(args.get("content", ""), "fact_store")
                return _rpc_response(request_id, {"status": "added"})

            if action == "search":
                results = vs(args.get("query", ""), limit=10)
                shadow_hits = _ss(args.get("query", ""), limit=10)
                return _rpc_response(request_id, {
                    "fts_results": [{"line": ln, "text": txt[:200]} for ln, _, txt in results],
                    "shadow_boosted": [{"line": ln, "trust": score} for score, ln in shadow_hits],
                })

            if action == "probe":
                shadow_hits = _ss(args.get("entity", ""), limit=20)
                return _rpc_response(request_id,
                                     [{"line": ln, "trust": score} for score, ln in shadow_hits])

            return _rpc_error(request_id, -32601, f"Unknown fact_store action: {action}")

        elif name == "fact_feedback":
            from nexsandglass.features.shadow_sand import shadow_feedback
            result = shadow_feedback(args.get("line_num", 0), args.get("helpful", True))
            return _rpc_response(request_id, result)

        elif name == "sandglass_dejavu":
            from nexsandglass.interfaces.nyx import nyx_gaze, nyx_hunt, nyx_sense
            action = args.get("action", "check")
            query = args.get("query", "")
            if action == "stats":
                return _rpc_response(request_id, nyx_gaze())
            if action == "hunt":
                return _rpc_response(request_id, nyx_hunt(query))
            return _rpc_response(request_id, nyx_sense(query))

        else:
            return _rpc_error(request_id, -32601, f"Unknown tool: {name}")

    except Exception as e:
        return _rpc_error(request_id, -32000, str(e))


def main():
    """MCP stdio 主循环"""
    for line in sys.stdin:
        try:
            req = json.loads(line.strip())
            method = req.get("method", "")

            # JSON-RPC 2.0 spec: messages without "id" are notifications.
            # Servers MUST NOT reply to notifications. The MCP handshake sends
            # `notifications/initialized` right after `initialize`; replying to
            # it with a fake id=0 corrupts subsequent response correlation and
            # breaks strict clients (opencode / Claude Desktop / Cursor).
            if "id" not in req:
                continue
            tid = req["id"]

            if method == "tools/list":
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

                tools = [
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
                ]
                print(_rpc_response(tid, {"tools": tools}), flush=True)

            elif method == "tools/call":
                name = req.get("params", {}).get("name", "")
                args = req.get("params", {}).get("arguments", {})
                print(_handle_tool(name, args, tid), flush=True)

            elif method == "initialize":
                print(_rpc_response(tid, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "NexSandglass", "version": __version__}
                }), flush=True)

            else:
                print(_rpc_error(tid, -32601, f"Unknown method: {method}"), flush=True)

        except json.JSONDecodeError:
            print(_rpc_error(0, -32700, "Parse error"), flush=True)
        except Exception as e:
            print(_rpc_error(0, -32000, str(e)), flush=True)


if __name__ == "__main__":
    main()
