#!/usr/bin/env python3
"""Фейковый `claude setup-token` для тестов.

Печатает ссылку так же, как настоящий CLI: разорванной по строкам, потому что
именно на этом разборе ломалась авторизация.
"""
import sys

URL = (
    "https://claude.com/cai/oauth/authorize?code=true&client_id=demo&response_type=code"
    "&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"
    "&scope=user%3Ainference&code_challenge=abc123&code_challenge_method=S256&state=xyz789"
)

print("Welcome to Claude Code")
print("Browser didn't open? Use the url below")
for start in range(0, len(URL), 60):
    print(URL[start:start + 60])
sys.stdout.write("Paste code here > ")
sys.stdout.flush()

code = sys.stdin.readline().strip()
print("\n" + ("sk-ant-oat01-" + "T" * 40 if code == "good-code" else "Invalid code"))
sys.stdout.flush()
