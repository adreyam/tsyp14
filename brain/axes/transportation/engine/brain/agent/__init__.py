"""City Brain - Transportation agent: the Brain as a set of tools that an orchestrator can call at any time.

    agent/tools.py       the tools (name, description, JSON schema, handler) - one place, three ways to reach them
    agent/rest.py        HTTP: GET /agent/manifest, POST /agent/invoke, GET /.well-known/agent.json   (mounted in the Brain)
    agent/mcp_server.py  MCP server (stdio or streamable HTTP) for orchestrators that speak the Model Context Protocol
    agent/client.py      tiny Python client (BrainAgent) for orchestrators written in Python
See AGENT.md.
"""
