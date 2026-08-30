"""Mock ACP agent para tests.

Implementa el protocolo ACP sobre stdio con respuestas predefinidas.
Permite testear el server sin un agente real.
"""
import json
import sys
import time
import threading


class MockACPAgent:
    """Agente ACP mock que responde a JSON-RPC sobre stdio."""

    def __init__(self):
        self._id = 0
        self._running = True
        self._sessions = {}
        self._lock = threading.Lock()

    def run(self):
        """Loop principal: lee stdin, procesa, escribe stdout."""
        reader = threading.Thread(target=self._read_loop, daemon=True)
        reader.start()
        reader.join()

    def _read_loop(self):
        while self._running:
            line = sys.stdin.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._handle(msg)

    def _send(self, msg):
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    def _send_notification(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send_response(self, rid, result):
        self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    def _send_error(self, rid, code, message):
        self._send({"jsonrpc": "2.0", "id": rid,
                    "error": {"code": code, "message": message}})

    def _handle(self, msg):
        method = msg.get("method", "")
        rid = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            self._send_response(rid, {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": True,
                    "promptCapabilities": {"image": False, "audio": False},
                    "sessionCapabilities": {"list": {}, "delete": {}},
                },
                "authMethods": [{"id": "mock-auth", "name": "Mock"}],
                "agentInfo": {"name": "mock-agent", "title": "Mock", "version": "1.0"},
            })

        elif method == "authenticate":
            self._send_response(rid, {})

        elif method == "session/new":
            sid = f"mock-session-{self._id}"
            self._id += 1
            self._sessions[sid] = {"cwd": params.get("cwd", "/")}
            self._send_response(rid, {"sessionId": sid})
            # Si hay prompt inicial, procesarlo
            prompt = params.get("prompt", [])
            if prompt:
                self._process_prompt(sid, prompt)

        elif method == "session/load":
            sid = params.get("sessionId", "")
            self._sessions[sid] = {"cwd": params.get("cwd", "/")}
            # Replay: enviar algunos mensajes de historial
            self._send_notification("session/update", {
                "sessionId": sid,
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "messageId": "msg_user_1",
                    "content": {"type": "text", "text": "Hola"},
                },
            })
            self._send_notification("session/update", {
                "sessionId": sid,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "messageId": "msg_agent_1",
                    "content": {"type": "text", "text": "Hola!"},
                },
            })
            self._send_response(rid, None)

        elif method == "session/prompt":
            sid = params.get("sessionId", "")
            # Procesar en background para no bloquear
            t = threading.Thread(target=self._process_prompt,
                                 args=(sid, params.get("prompt", []), rid), daemon=True)
            t.start()

        elif method == "session/cancel":
            # Notificación, no response
            pass

        elif method == "session/list":
            sessions = [{"sessionId": s, "cwd": v["cwd"], "title": f"Mock {s}"}
                        for s, v in self._sessions.items()]
            self._send_response(rid, {"sessions": sessions})

        else:
            if rid is not None:
                self._send_error(rid, -32601, f"Method not found: {method}")

    def _process_prompt(self, sid, prompt, rid=None):
        """Simula el procesamiento de un prompt con streaming."""
        time.sleep(0.1)  # simular latencia

        # Enviar thought
        self._send_notification("session/update", {
            "sessionId": sid,
            "update": {
                "sessionUpdate": "agent_thought_chunk",
                "messageId": "msg_thought_1",
                "content": {"type": "text", "text": "Pensando..."},
            },
        })
        time.sleep(0.1)

        # Enviar tool call
        self._send_notification("session/update", {
            "sessionId": sid,
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "tc_1",
                "title": "Reading file",
                "kind": "read",
                "status": "pending",
            },
        })
        time.sleep(0.1)

        self._send_notification("session/update", {
            "sessionId": sid,
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tc_1",
                "status": "in_progress",
            },
        })
        time.sleep(0.1)

        self._send_notification("session/update", {
            "sessionId": sid,
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tc_1",
                "status": "completed",
                "content": [{"type": "content",
                             "content": {"type": "text", "text": "File content here"}}],
            },
        })
        time.sleep(0.1)

        # Enviar message chunks
        chunks = ["OK", " Hecho.", ""]
        for chunk in chunks:
            self._send_notification("session/update", {
                "sessionId": sid,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "messageId": "msg_agent_2",
                    "content": {"type": "text", "text": chunk},
                },
            })
            time.sleep(0.05)

        # State update: idle
        self._send_notification("session/update", {
            "sessionId": sid,
            "update": {
                "sessionUpdate": "state_update",
                "state": "idle",
                "stopReason": "end_turn",
            },
        })

        # Response al prompt
        if rid is not None:
            self._send_response(rid, {"stopReason": "end_turn"})


if __name__ == "__main__":
    agent = MockACPAgent()
    agent.run()
