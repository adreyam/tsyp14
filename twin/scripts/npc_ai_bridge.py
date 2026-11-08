#!/usr/bin/env python3
"""Local, budget-limited OpenRouter planner for City Ground Zero NPCs."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_MODEL = "qwen/qwen3-30b-a3b-instruct-2507"
LEGAL_INTENTS = {"commute_to_work", "perform_job", "socialize", "run_errand", "return_home", "observe_traffic"}
LEGAL_DESTINATIONS = {"north", "east", "south", "west"}


class Planner:
    def __init__(self) -> None:
        self.key = os.environ.get("OPENROUTER_API_KEY", "")
        self.model = os.environ.get("OPENROUTER_NPC_MODEL", DEFAULT_MODEL)
        self.max_calls_per_hour = max(1, int(os.environ.get("NPC_AI_MAX_CALLS_PER_HOUR", "36")))
        self.calls: list[float] = []
        self.lock = threading.Lock()

    def fallback(self, payload: dict, source: str = "local") -> dict:
        sides = [side for side in payload.get("available_destinations", []) if side in LEGAL_DESTINATIONS] or sorted(LEGAL_DESTINATIONS)
        token = sum(ord(character) for character in str(payload.get("npc_id", "npc")))
        intents = [intent for intent in payload.get("legal_intents", []) if intent in LEGAL_INTENTS]
        current_intent = str(payload.get("current_state", {}).get("intent", ""))
        intent = current_intent if current_intent in LEGAL_INTENTS else (intents[token % len(intents)] if intents else "perform_job")
        legal_actions = [str(value) for value in payload.get("legal_actions", [])[:4] if str(value)]
        return {
            "intent": intent,
            "destination_side": sides[token % len(sides)],
            "next_action_id": legal_actions[token % len(legal_actions)] if legal_actions else "",
            "duration_s": 60,
            "source": source,
        }

    def _may_call(self) -> bool:
        now = time.monotonic()
        with self.lock:
            self.calls = [moment for moment in self.calls if now - moment < 3600.0]
            if len(self.calls) >= self.max_calls_per_hour:
                return False
            self.calls.append(now)
            return True

    def plan(self, payload: dict) -> dict:
        if not self.key:
            return self.fallback(payload, "local_no_key")
        if not self._may_call():
            return self.fallback(payload, "local_budget")
        destinations = [side for side in payload.get("available_destinations", []) if side in LEGAL_DESTINATIONS]
        legal_actions = [str(value) for value in payload.get("legal_actions", [])[:4] if str(value)]
        if not destinations:
            return self.fallback(payload, "local_invalid_request")
        schema = {
            "name": "npc_sidewalk_intent",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "enum": sorted(LEGAL_INTENTS)},
                    "destination_side": {"type": "string", "enum": destinations},
                    "duration_s": {"type": "integer", "minimum": 20, "maximum": 120},
                    "next_action_id": {"type": "string", "enum": legal_actions},
                },
                "required": ["intent", "destination_side", "duration_s", "next_action_id"],
                "additionalProperties": False,
            },
        }
        request_body = {
            "model": self.model,
            "temperature": 0.35,
            "max_tokens": 80,
            "messages": [
                {
                    "role": "system",
                    "content": "You advise a simulated city resident. Respect their stated role, personality and current work schedule. Choose only a listed legal intent and sidewalk destination; never invent coordinates, tools, hazards or extra fields.",
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "npc_id": payload.get("npc_id", "npc"), "role": payload.get("role", "resident"),
                        "personality": payload.get("personality", ""), "active_task": payload.get("active_task", ""), "task_target": payload.get("task_target", ""), "block": payload.get("block", ""),
                        "current_side": payload.get("current_side", "north"), "available_destinations": destinations, "legal_actions": legal_actions,
                    }),
                },
            ],
            "response_format": {"type": "json_schema", "json_schema": schema},
            "provider": {"require_parameters": True, "data_collection": "deny", "sort": "price"},
        }
        try:
            request = Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(request_body).encode("utf-8"),
                headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=6.0) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            plan = json.loads(content) if isinstance(content, str) else content
            if not isinstance(plan, dict):
                raise ValueError("model plan is not an object")
            if plan.get("intent") not in LEGAL_INTENTS or plan.get("destination_side") not in destinations or plan.get("next_action_id") not in legal_actions:
                raise ValueError("model plan contains an illegal world action")
            plan["duration_s"] = max(20, min(120, int(plan.get("duration_s", 60))))
            plan["source"] = "openrouter"
            return plan
        except (KeyError, TypeError, ValueError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            return self.fallback(payload, "local_api_fallback")


def make_handler(planner: Planner):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(404)
                return
            self._send({"status": "ok", "remote_enabled": bool(planner.key), "model": planner.model})

        def do_POST(self) -> None:
            if self.path != "/plan":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(min(length, 8192)).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("request must be an object")
            except (ValueError, json.JSONDecodeError):
                self.send_error(400)
                return
            self._send(planner.plan(payload))

        def _send(self, value: dict) -> None:
            encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8816)
    args = parser.parse_args()
    planner = Planner()
    print("NPC planner: %s (%s)" % ("OpenRouter enabled" if planner.key else "deterministic local fallback", planner.model), flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler(planner)).serve_forever()


if __name__ == "__main__":
    main()
