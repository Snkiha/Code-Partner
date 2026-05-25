# Start with a lightweight Python image
FROM python:3.11-slim

# Install Node.js (required for the npx filesystem MCP server)
RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Set up the internal application directory
WORKDIR /app

# Copy the dependency list and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your agent scripts into the container
# NOTE: Replace 'agent.py' with whatever you named your main file
COPY agent.py bash_mcp_server.py ./

# Create a designated "sandbox" directory for the agent to work in.
# We will mount the user's local code directory here.
RUN mkdir /workspace
WORKDIR /workspace

# Set environment variables
# Tell the Ollama client to look for the host machine's Ollama instance, not localhost inside the container
ENV OLLAMA_HOST=http://host.docker.internal:11434

# Run the agent script from the /app directory while working inside /workspace
CMD ["python", "/app/agent.py"]