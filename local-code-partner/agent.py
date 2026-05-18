import asyncio
import os
from ollama import Client
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule

console = Console()
ollama_client = Client()

MODEL_NAME = "qwen2.5-coder:3b"
WRITER_MODEL = "qwen2.5-coder:3b"
REVIEWER_MODEL = "qwen2.5-coder:3b"
CHAT_MODEL = "qwen2.5-coder:3b"

# Define the MCP Server parameters
server_params = StdioServerParameters(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", os.getcwd()]
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

async def _run_tool_loop(session: ClientSession, ollama_tools: list, messages: list, model: str, label: str, *, max_rounds: int = 6) -> str:
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
                result = await session.call_tool(name, arguments=args)
                result_text = "".join(getattr(item, "text", str(item)) for item in result.content)
            except Exception as exc:
                result_text = f"Tool error: {exc}"
                console.print(f"[red]Tool error: {exc}[/red]")
            
            messages.append({"role": "tool", "content": result_text, "name": name})
    
    # Max rounds hit - ask for final summary with no tools
    final = ollama_client.chat(model=model, messages=messages, options={"num_ctx": 16384})
    return final.message.content or ""

# -- WRITER AGENT -- #
async def run_writer(session, ollama_tools, user_request: str) -> str:
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
    response_text = await _run_tool_loop(session, ollama_tools, messages, WRITER_MODEL, "Writer")
    console.print("\n[bold cyan]Writer:[/bold cyan]")
    console.print(Markdown(response_text or "No commentary."))
    return response_text # Caller will extract filename

# -- REVIEWER AGENT -- #
async def run_reviewer(session, ollama_tools, filename: str) -> None:
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
    response_text = await _run_tool_loop(session, ollama_tools, messages, REVIEWER_MODEL, "Reviewer")
    
    console.print("\n[bold magenta]Reviewer:[/bold magenta]")
    console.print(Markdown(response_text or "*No commentary.*"))
    console.print(f"\n[green]✓ Review saved to [bold]{review_file}[/bold][/green]")

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

async def review_mode(session, ollama_tools, user_request: str) -> None:
    writer_output = await run_writer(session, ollama_tools, user_request)
    
    filename = _extract_filename(writer_output)
    if not filename:
        console.print("[red]Could not determine the filename from the Writer's response. ""Skipping review step.[/red]")
        return

    console.print(f"\n[dim]Writer saved: [bold]{filename}[/bold]. Handing off to Reviewer…[/dim]")
    await run_reviewer(session, ollama_tools, filename)
    
    
async def main():
    # Establish connection to MCP Server
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # Fetch tools exposed by the MCP Server
            mcp_tools = await session.list_tools()
            
            # Format MCP tools into the structural format Ollama expects
            ollama_tools = []
            for tool in mcp_tools.tools:
                ollama_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.inputSchema
                    }
                })
            
            # System prompt optimized for a terminal developer environment
            messages = [{
                "role": "system",
                "content": "You are an elite terminal-based pair programmer. You have access to filesystem tools. Use them to view, edit, or create files when requested. Be concise, direct, and output production-ready code."
            }]
            
            console.print(Panel("[bold green]Local Coding Partner Initialized![/bold green]\nType your prompt below. Type 'exit' to quit.", title="System"))
            
            # Interactive Terminal Loop
            while True:
                user_input = console.input("\n[bold blue]You:[/bold blue]")
                if user_input.lower() in ["exit", "quit"]:
                    break
                
                messages.append({"role": "user", "content": user_input})
                
                # Call Ollama
                response = ollama_client.chat(
                    model=MODEL_NAME,
                    messages=messages,
                    tools=ollama_tools,
                    options={"num_ctx": 16384, "temperature": 0.1} # Forces a larger memory buffer
                )
                
                # Check if the model decided to call an MCP tool
                if response.message.tool_calls:
                    # Append the model's intent to call the tool to history first
                    messages.append(response.message)
                    
                    for tool_call in response.message.tool_calls:
                        tool_name = tool_call.function.name
                        tool_args = tool_call.function.arguments
                        
                        console.print(f"[yellow]⚡ Agent calling tool '{tool_name}' with args: {tool_args}[/yellow]")
                        
                        # Execute the tool via MCP Session
                        result = await session.call_tool(tool_name, arguments=tool_args)
                        
                        # Extract string representation from MCP response content items
                        result_text = "".join([getattr(item, 'text', str(item)) for item in result.content])
                        
                        # Add the tool execution result back to the LLM conversation history
                        messages.append({
                            "role": "tool",
                            "content": result_text,
                            "name": tool_name
                        })
                        
                    # FIX: Get final response and reference 'final_response' instead of 'response'
                    final_response = ollama_client.chat(
                        model=MODEL_NAME,
                        messages=messages,
                        options={"num_ctx": 16384}
                    )
                    console.print("\n[bold magenta]Coding Partner:[/bold magenta]")
                    console.print(Markdown(final_response.message.content or "*Executed tool successfully but returned no commentary.*"))
                    messages.append(final_response.message)
                else:
                    # Standard conversational response
                    console.print("\n[bold magenta]Coding Partner:[/bold magenta]")
                    console.print(Markdown(response.message.content or "No response context found."))
                    messages.append(response.message)

if __name__ == "__main__":
    asyncio.run(main())