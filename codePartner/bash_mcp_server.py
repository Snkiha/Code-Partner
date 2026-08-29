"""
bash_mcp_server.py — A minimal MCP server that exposes a controlled
subprocess execution tool to any MCP-compatible client.

Tools exposed:
    • run_command(command, timeout, working_dir)
        Runs a shell command, captures stdout + stderr, returns both with
        the exit code. The client (LLM) can see errors and patch its own code.

Security notes (read before deploying):
    • ALLOWED_COMMANDS whitelist restricts which executables may run.
    Add or remove entries to match your project's needs.
    • working_dir is restricted to CWD_ROOT (defaults to the directory
    this server is launched from). Path traversal attempts are rejected.
    • Shell interpolation is disabled (shell=False via shlex.split).
    • Set BASH_MCP_ALLOW_ALL=1 env var ONLY in fully trusted local environments
    to skip the whitelist (useful during dev, dangerous in production).
"""

import asyncio
import os
import shlex
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# -- CONFIGURATION -- #
CWD_ROOT = Path(os.getcwd()).resolve()
DEFAULT_TIMEOUT = 30 # 30 seconds
MAX_OUTPUT_CHARS = 12_000 # truncate runaway output

ALLOW_ALL = os.getenv("BASH_MCP_ALLOW_ALL", "0") == "1"

# Executables the LLM is allowed to invoke
ALLOWED_COMMANDS: set[str] = {
    "python",
    "node",
    "npm",
    "npx",
    "pytest",
    "pip",
    "pip3",
    "ruff",
    "mypy",
    "black",
    "isort",
    "eslint",
    "tsc",
    "cargo",
    "go",
    "make",
    "ls",
    "cat",
    "echo",
    "grep",
    "find",
    "head",
    "tail",
    "diff"
}

# -- SERVER SETUP-- #

app = Server("bash-execution-mcp")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name = "run_command",
            description=(
                "Execute a shell command in the project sandbox and return its "
                "stdout, stderr, and exit code. Use this to run Python scripts, "
                "tests, linters, or any CLI tool. Read the output to detect errors "
                "and fix your code. Commands are restricted to a safe allowlist."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The full command to run, e.g. 'python feature.py' "
                            "or 'pytest tests/ -v'. Do NOT use shell operators "
                            "like &&, |, ;, >, or $()."
                        ),
                    },
                    "working_dir": {
                        "type": "string",
                        "description": (
                            "Subdirectory to run in (relative to project root). "
                            "Defaults to the project root. Must not escape the root."
                        ),
                        "default": ".",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": f"Seconds before the command is killed. Default {DEFAULT_TIMEOUT}.",
                        "default": DEFAULT_TIMEOUT,
                    },
                },
                "required": ["command"],
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "run_command":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    
    command: str = arguments.get("command", "").strip()
    working_dir: str = arguments.get("working_dir", ".")
    timeout: int = int(arguments.get("timeout", DEFAULT_TIMEOUT))
    
    # Validate the commands
    if not command:
        return [TextContent(type="text", text="Error: empty command.")]
    
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return [TextContent(type="text", text=f"Error parsing command: {exc}")]
    
    executable = Path(parts[0]).name # strip any path prefix
    if not ALLOW_ALL and executable not in ALLOWED_COMMANDS:
        allowed_list = ", ".join(sorted(ALLOWED_COMMANDS))
        return [TextContent(
            type="text",
            text=(
                f"Error: '{executable}' is not in the allowed-commands list.\n"
                f"Allowed: {allowed_list}\n"
                f"Set BASH_MCP_ALLOW_ALL=1 to disable this restriction."
            )
        )]
        
    # Validate Working Directory
    try:
        run_dir = (CWD_ROOT / working_dir).resolve()
        run_dir.relative_to(CWD_ROOT) # raises ValueError if escaping
    except ValueError:
        return [TextContent(
            type="text",
            text=f"Error: working_dir '{working_dir}' escapes the project root."
        )]
    
    # Execute
    try:
        proc = await asyncio.create_subprocess_exec(
            *parts,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(run_dir)
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return [TextContent(
                type="text",
                text=f"Error: command timed out after {timeout}s.\nPartial stdout may be lost."
            )]
    except FileNotFoundError:
        return [TextContent(type="text", text=f"Error: executable '{parts[0]}' not found on PATH.")]
    except Exception as exc:
        return [TextContent(type="text", text=f"Error launching process: {exc}")]
    
    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    rc = proc.returncode
    
    # Truncate runaway output so it does not flood the context window.
    # Keep the head AND the tail: Python tracebacks and test-failure summaries
    # live at the end of the stream, so a head-only cut hides the actual error
    # the Healer needs to see.
    def _truncate(text: str, label: str) -> str:
        if len(text) <= MAX_OUTPUT_CHARS:
            return text
        head_chars = MAX_OUTPUT_CHARS // 3
        tail_chars = MAX_OUTPUT_CHARS - head_chars
        omitted = len(text) - head_chars - tail_chars
        return (
            f"{text[:head_chars]}\n\n"
            f"[{label} truncated — {omitted} chars omitted from the middle]\n\n"
            f"{text[-tail_chars:]}"
        )
    
    stdout = _truncate(stdout, "stdout")
    stderr = _truncate(stderr, "stderr")
    
    result = (
        f"Exit code: {rc}\n"
        f"Working dir: {run_dir}\n"
        f"Command: {command}\n"
        f"{'─' * 40}\n"
        f"STDOUT:\n{stdout or '(empty)'}\n"
        f"{'─' * 40}\n"
        f"STDERR:\n{stderr or '(empty)'}"
    )
    
    return [TextContent(type="text", text=result)]

# -- ENTRYPOINT -- #
async def _main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(_main())