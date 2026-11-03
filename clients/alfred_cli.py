#!/usr/bin/env python3
"""Talk to Alfred from a terminal (any computer, SSH session, tmux pane).

    pip install httpx
    python alfred_cli.py --server https://alfred.your-tailnet.ts.net --token <ALFRED_API_TOKEN>
    python alfred_cli.py "What's on today?"          # one question, then exit

Environment variables ALFRED_SERVER and ALFRED_TOKEN work too. No dependency on the server code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def stream_reply(client: httpx.Client, message: str, conversation_id: str | None) -> str | None:
    body = {"message": message, "conversation_id": conversation_id}
    with client.stream("POST", "/v1/chat", json=body, timeout=None) as resp:
        if resp.status_code == 401:
            sys.exit("The server rejected the token.")
        if resp.status_code == 404:
            return stream_reply(client, message, None)
        resp.raise_for_status()
        print("Alfred: ", end="", flush=True)
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            conversation_id = event.get("conversation_id") or conversation_id
            if event["type"] == "token":
                print(event["data"]["text"], end="", flush=True)
            elif event["type"] == "error":
                print(f"\n[{event['data']['code']}] {event['data']['message']}", end="")
        print()
    return conversation_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("message", nargs="*", help="ask once and exit")
    parser.add_argument(
        "--server", default=os.environ.get("ALFRED_SERVER", "http://127.0.0.1:8000")
    )
    parser.add_argument("--token", default=os.environ.get("ALFRED_TOKEN", ""))
    args = parser.parse_args()
    if not args.token:
        sys.exit("Give the access token with --token or ALFRED_TOKEN.")
    client = httpx.Client(
        base_url=args.server.rstrip("/"), headers={"Authorization": f"Bearer {args.token}"}
    )
    if args.message:
        stream_reply(client, " ".join(args.message), None)
        return
    conversation_id = None
    print("Talk to Alfred. Ctrl-D to leave.")
    while True:
        try:
            message = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if message:
            conversation_id = stream_reply(client, message, conversation_id)


if __name__ == "__main__":
    main()
