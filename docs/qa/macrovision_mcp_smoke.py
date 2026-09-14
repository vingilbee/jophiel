"""Smoke-test MacroVision's actual standalone MCP stdio transport."""
import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command="uv",
        args=["run", "--no-sync", "python", "-m", "app.ai.tools.macrovision.server"],
        env={**os.environ, "MACROVISION_USER_ID": os.environ["MACROVISION_USER_ID"]},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            print("TOOLS", [tool.name for tool in listed.tools])
            result = await session.call_tool(
                "macrovision_list_food_photos", {"limit": 1}
            )
            print("RESULT", result.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
