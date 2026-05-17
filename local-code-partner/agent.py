import asyncio
import os
from ollama import Client
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

console = Console()
ollama_client = Client()
MODEL_NAME = "qwen2.5-coder:3b"

# Define the MCP Server parameters
server_params = StdioServerParameters(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", os.getcwd()]
)

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