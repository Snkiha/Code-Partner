import asyncio
import os
from ollama import Client
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from pathlib import Path

console = Console()
ollama_client = Client()

WRITER_MODEL = "llama3.1:8b"
REVIEWER_MODEL = "qwen2.5-coder:7b"
CHAT_MODEL = "qwen2.5-coder:3b"

MAX_HEAL_ROUNDS = 5 # max write->output cycles before giving up

# Define the MCP Server parameters
server_params = StdioServerParameters(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", os.getcwd()]
)

bash_server_params = StdioServerParameters(
    command="python",
    args=["bash_mcp_server.py"] # live alongside this file
)

# --HELPER FUNCTIONS-- #

def _tool_schema(mcp_tools):
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        }
        for tool in mcp_tools.tools
    ]

def _merge_tools(*schemas: list) -> list:
    seen, merged = set(), []
    for schema in schemas:
        for tool in schema:
            name = tool["function"]["name"]
            if name not in seen:
                seen.add(name)
                merged.append(tool)
    return merged

async def _call_tool(sessions: dict, name: str, args: dict) -> str:
    for session in sessions.values():
        try:
            result = await session.call_tool(name, arguments=args)
            return "".join(getattr(item, "text", str(item)) for item in result.content)
        except Exception:
            continue
    return f"Error: no session could handle tool '{name}'"

async def _run_tool_loop(sessions: dict, ollama_tools: list, messages: list, model: str, label: str, *, max_rounds: int = 6) -> str:
    """
    Drive a single agent through up to 'max_rounds' of tool-call cycles.
    Returns the agent's final text response.
    """
    for _ in range(max_rounds):
        response = ollama_client.chat(
            model=model,
            messages=messages,
            tools=ollama_tools,
            options={"num_ctx": 16384, "temperature": 0.1} # Forces a larger memory buffer
        )
        
        if not response.message.tool_calls:
            content = response.message.content or ""
            messages.append(response.message)
            return content
        
        # Agents want to call one or more tools
        messages.append(response.message)
        for tc in response.message.tool_calls:
            name = tc.function.name
            args = tc.function.arguments
            console.print(f"[yellow]⚡ [{label}] calling '{name}' → {args}[/yellow]")
            try:
                result_text = await _call_tool(sessions, name, args)
            except Exception as exc:
                result_text = f"Tool error: {exc}"
                console.print(f"[red]Tool error: {exc}[/red]")
            
            messages.append({"role": "tool", "content": result_text, "name": name})
    
    # Max rounds hit - ask for final summary with no tools
    final = ollama_client.chat(model=model, messages=messages, options={"num_ctx": 16384})
    return final.message.content or ""

# -- WRITER AGENT -- #
async def run_writer(sessions, ollama_tools, user_request: str) -> str:
    """
    Writer Agent: Generates code and save it to disk via MCP.
    Returns the filename it saved.
    """
    console.print(Rule("[bold cyan]Writer agent[/bold cyan]"))
    
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert software engineer. "
                "When asked to implement a feature, write clean, production-ready Python code. "
                "Always save your output to a file in the current directory using the write_file tool. "
                "Choose a descriptive snake_case filename ending in .py. "
                "After saving, state the exact filename you used."
            )
        },
        {"role": "user", "content": user_request}
    ]
    response_text = await _run_tool_loop(sessions, ollama_tools, messages, WRITER_MODEL, "Writer")
    console.print("\n[bold cyan]Writer:[/bold cyan]")
    console.print(Markdown(response_text or "No commentary."))
    return response_text # Caller will extract filename

# -- REVIEWER AGENT -- #
async def run_reviewer(sessions, ollama_tools, filename: str) -> None:
    """
    Reviewer Agent: Read the written file, critique it, write a .review.md file.
    """
    console.print(Rule("[bold magenta]Reviewer agent[/bold magenta]"))
    
    review_file = filename.rsplit(".", 1)[0] + ".review.md"
    
    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior security-focused code reviewer. "
                "Use read_file to read the source file you are given, then write a thorough review covering:\n"
                "1. Security vulnerabilities (injection, path traversal, auth issues, secrets in code, etc.)\n"
                "2. Correctness bugs and edge cases\n"
                "3. Performance and resource concerns\n"
                "4. Code quality and maintainability\n"
                "5. A VERDICT: APPROVED / APPROVED_WITH_NOTES / NEEDS_CHANGES\n\n"
                f"Save the review as '{review_file}' using write_file. "
                "Use Markdown with clear headings for each section."
            )
        },
        {
            "role": "user",
            "content": f"Please review the file: {filename}"
        }
    ]
    response_text = await _run_tool_loop(sessions, ollama_tools, messages, REVIEWER_MODEL, "Reviewer")
    
    console.print("\n[bold magenta]Reviewer:[/bold magenta]")
    console.print(Markdown(response_text or "*No commentary.*"))
    console.print(f"\n[green]✓ Review saved to [bold]{review_file}[/bold][/green]")

async def run_execute_heal(sessions, ollama_tools, filename: str, run_cmd: str) -> None:
    """
    Self-healing loop:
    1. Run the script with run_command.
    2. If exit code != 0, feed the error back to the Writer to patch the file.
    3. Repeat up to MAX_HEAL_ROUNDS times.
    """
    console.print(Rule("[bold green]Executor + Self-Heal loop[/bold green]"))
    
    messages = [
        {
            "role": "system",
            "content": (
                "You are an autonomous debugging engineer. "
                "Use run_command to execute the script. "
                "If it fails (non-zero exit code), read the error output carefully, "
                "use read_file to inspect the code, then use write_file to patch exactly "
                "the broken lines. Do NOT rewrite the entire file unless necessary. "
                "After patching, run the command again. "
                f"Repeat until exit code is 0 or you have tried {MAX_HEAL_ROUNDS} times. "
                "Give a concise summary of what was fixed when done."
            )
        },
        {
            "role": "user",
            "content": (
                f"Script: {filename}\n"
                f"Run command: {run_cmd}\n\n"
                "Start by running the command and report the result."
            )
        }
    ]
    
    for attempt in range(1, MAX_HEAL_ROUNDS + 1):
        console.print(f"\n[dim]── Attempt {attempt}/{MAX_HEAL_ROUNDS} ──[/dim]")
        output = await _run_tool_loop(sessions, ollama_tools, messages, WRITER_MODEL, f"Heal-{attempt}", max_rounds=4)
        
        console.print(Markdown(output or ""))
            
        last_tool_results = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        if last_tool_results and "Exit code: 0" in last_tool_results[-1]["content"]:
            console.print(Panel(f"[bold green]✓ Script ran successfully after {attempt} attempt(s).[/bold green]", title="Self-Heal"))
            return
        
        messages.append({
            "role": "user",
            "content": "Still failing. Patch the code and run it again."
        })
    
    console.print(Panel(f"[bold red]✗ Still failing after {MAX_HEAL_ROUNDS} attempts.[/bold red]\n"
        "Check the error output above for manual inspection.", title="Self-Heal"))

async def run_mode(sessions, ollama_tools, user_request: str) -> None:
    writer_output = await run_writer(sessions, ollama_tools, user_request)
    filename = _extract_filename(writer_output)
    if not filename:
        console.print("[red]Could not determine the filename from Writer response.[/red]")
        return

    run_cmd = f"python {filename}"
    console.print(f"\n[dim]Running [bold]{run_cmd}[/bold]…[/dim]")
    await run_execute_heal(sessions, ollama_tools, filename, run_cmd)

# -- PIN STORE -- #

pins: list[dict] = []

def _pin_add(raw_path: str) -> str:
    path = Path(raw_path).resolve()
    if not path.is_file():
        return f"[red]Not a file: {raw_path}[/red]"
    # if already pinned
    if any(p["path"] == str(path) for p in pins):
        return f"[yellow]Already pinned: {path.name}[/yellow]"
    try:
        content = path.read_text(errors="replace")
    except Exception as exc:
        return f"[red]Could not read {raw_path}: {exc}[/red]"
    pins.append({"path": str(path), "content": content, "name": path.name})
    return f"[green]Pinned: {path.name} ({len(content):,} chars)[/green]"

def _pin_remove(raw_path: str) -> str:
    path = str(Path(raw_path).resolve())
    before = len(pins)
    pins[:] = [p for p in pins if p["path"] != path]
    if len(pins) == before:
        return f"[yellow]Not pinned: {raw_path}[/yellow]"
    return f"[green]Unpinned: {Path(raw_path).name}[/green]"
    
# -- REVIEW-MODE ORCHESTRATOR -- #

def _extract_filename(writer_text: str) -> str | None:
    """
    Best-Effort extraction of the .py filename from the writer's response.
    Looks for any token ending in .py.
    """
    for token in writer_text.split():
        cleaned = token.strip("`,.'\"()")
        if cleaned.endswith(".py"):
            return cleaned
    return None

async def review_mode(sessions, ollama_tools, user_request: str) -> None:
    writer_output = await run_writer(sessions, ollama_tools, user_request)
    
    filename = _extract_filename(writer_output)
    if not filename:
        console.print("[red]Could not determine the filename from the Writer's response. ""Skipping review step.[/red]")
        return

    console.print(f"\n[dim]Writer saved: [bold]{filename}[/bold]. Handing off to Reviewer…[/dim]")
    await run_reviewer(sessions, ollama_tools, filename)
    
    
async def main():
    async with stdio_client(server_params) as (read_stream, write_stream), \
            stdio_client(bash_server_params) as (bash_read, bash_write):

        async with ClientSession(read_stream, write_stream) as fs_session, \
                ClientSession(bash_read, bash_write) as bash_session:

            await fs_session.initialize()
            await bash_session.initialize()

            all_sessions = {"fs": fs_session, "bash": bash_session}

            fs_tools   = _tool_schema(await fs_session.list_tools())
            bash_tools = _tool_schema(await bash_session.list_tools())
            all_tools  = _merge_tools(fs_tools, bash_tools)
        
            # System prompt optimized for a terminal developer environment
            messages = [{
                "role": "system",
                "content": (
                    "You are an elite terminal-based pair programmer with access to "
                    "filesystem tools (read/write files) and a bash execution tool "
                    "(run shell commands). Use them freely. Be concise and direct."
                    )
            }]
            
            console.print(Panel(
                    "[bold green]Local Coding Partner — Bash Sandbox Edition[/bold green]\n\n"
                    "[bold]--review[/bold]〈request〉→ Writer → Reviewer pipeline\n"
                    "[bold]--run[/bold]〈request〉→ Write → Execute → Self-Heal loop\n"
                    "Normal input → conversational agent\n\n"
                    "Type [bold]exit[/bold] to quit.",
                    title="System"
            ))
            
            # Interactive Terminal Loop
            while True:
                raw = console.input("\n[bold blue]You:[/bold blue]")
                if raw.lower().strip() in ("exit", "quit"):
                    break
                
                # Review Mode
                if raw.lstrip().startswith("--review"):
                    user_request = raw.lstrip().removeprefix("--review").strip()
                    if not user_request:
                        console.print("[yellow]Please describe what you want to build.[/yellow]")
                        continue
                    await review_mode(all_sessions, all_tools, user_request)
                    continue
                
                elif raw.startswith("--run"):
                    request = raw.removeprefix("--run").strip()
                    if not request:
                        console.print("[yellow]Usage: --run <what to build>[/yellow]")
                        continue
                    await run_mode(all_sessions, all_tools, request)
                
                else:
                    # Normal conversational/agentic mode
                    messages.append({"role": "user", "content": raw})
                    output = await _run_tool_loop(all_sessions, all_tools, messages, CHAT_MODEL, "Chat")
                    
                    console.print("\n[bold magenta]Coding Partner:[/bold magenta]")
                    console.print(Markdown(output or "No response."))

if __name__ == "__main__":
    asyncio.run(main())

