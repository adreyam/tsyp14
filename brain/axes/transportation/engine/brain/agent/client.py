"""Small client for orchestrators written in Python.

    from agent.client import BrainAgent
    brain = BrainAgent("http://127.0.0.1:8000", key="dev-agent-key")
    print(brain.tools())                              # names + schemas, e.g. to hand to an LLM as function definitions
    r = brain.call("get_city_status")                 # -> {"ok": True, "summary": "...", "data": {...}}
    brain.call("start_traffic_jam", cars=300)
"""
import requests


class BrainAgent:
    def __init__(self, url="http://127.0.0.1:8000", key="dev-agent-key", caller="orchestrator", timeout=60):
        self.url, self.key, self.caller, self.timeout = url.rstrip("/"), key, caller, timeout

    def _h(self):
        return {"X-Agent-Key": self.key}

    def tools(self):
        r = requests.get(self.url + "/agent/manifest", headers=self._h(), timeout=self.timeout)
        r.raise_for_status()
        return r.json()["tools"]

    def call(self, tool, request_id=None, **arguments):
        r = requests.post(self.url + "/agent/invoke", headers=self._h(), timeout=self.timeout,
                          json={"tool": tool, "arguments": arguments, "request_id": request_id, "caller": self.caller})
        r.raise_for_status()
        return r.json()

    def llm_tool_definitions(self):
        """Tool definitions in the shape most LLM APIs expect (name, description, input_schema)."""
        return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in self.tools()]
