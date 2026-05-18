# Standout Features to add
1. Sequential Multi-Agent "Reviewer" Mode
Don't just have one model write the code. When a user asks for a feature, use your framework to instantiate two internal roles:

The Writer: (qwen2.5-coder:7b) generates the script and saves it via the filesystem MCP.

The Reviewer: (llama3.1:8b) reads the saved script, reviews it for security flaws or optimizations, and automatically appends a .review.md file or chats back fixes to the Writer.

2. A Local "Bash Execution" Sandbox Server
Build or attach a standard command-line execution MCP server (like the community mcp-server-commands or building a basic one using Python's subprocess). This allows the LLM to write code, call a tool to execute python script.py or npm test, read the terminal error messages directly, and patch its own bugs without you lifting a finger.

3. Contextual File Pinning
In your CLI loop, catch special command syntax. For example, if a user types:
+pin ./src/auth.py
Your framework intercepts this string, loads the content of auth.py, and pins it statically as a "system context message" at the top of the chat stack so the model always acts with explicit awareness of your core codebase structure.
