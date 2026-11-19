"""Fake ousheng-mcp for tests: newline JSON-RPC, logs calls to argv[1]."""
import json
import sys

log = open(sys.argv[1], "a")
_next_id = 0


def reply(rid, result):
    msg = {"jsonrpc": "2.0", "id": rid, "result": result}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line.startswith("{"):
        continue
    req = json.loads(line)
    if "id" not in req:
        continue  # notification
    method = req["method"]
    log.write(json.dumps({"method": method, "params": req.get("params", {})}) + "\n")
    log.flush()
    if method == "initialize":
        reply(req["id"], {"protocolVersion": "2024-11-05",
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}})
    elif method == "tools/list":
        reply(req["id"], {"tools": [{"name": "create_work_item", "inputSchema": {}}]})
    elif method == "tools/call":
        name = req["params"]["name"]
        args = req["params"]["arguments"]
        if args.get("fail"):
            reply(req["id"], {"content": [{"type": "text", "text": "boom"}], "isError": True})
        elif name == "get_work_item":
            want = args.get("id")
            created = any(
                json.loads(l).get("params", {}).get("arguments", {}).get("id") == want
                for l in open(sys.argv[1])
                if '"create_work_item"' in l)
            if created:
                reply(req["id"], {"content": [{"type": "text", "text": f"item {want}"}],
                                  "isError": False})
            else:
                reply(req["id"], {"content": [{"type": "text", "text": "not found"}],
                                  "isError": True})
        else:
            reply(req["id"], {"content": [{"type": "text",
                                           "text": f"ok:{name}"}], "isError": False})
    else:
        reply(req["id"], {})
log.close()
