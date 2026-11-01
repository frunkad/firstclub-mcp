"""In-process MCP smoke test: uv run scripts/smoke.py <tool> [json-args]"""
import asyncio
import json
import sys
from fastmcp import Client
from firstclub_mcp.server import mcp

async def main():
    async with Client(mcp) as c:
        if len(sys.argv) < 2:
            for t in await c.list_tools():
                print(t.name, '-', (t.description or '').split('\n')[0])
            return
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        r = await c.call_tool(sys.argv[1], args, raise_on_error=False)
        out = r.structured_content if r.structured_content is not None else [getattr(x, 'text', x) for x in r.content]
        print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
asyncio.run(main())
