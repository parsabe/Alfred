"""`alfred`: Alfred on this computer, connected to your Alfred server.

alfred              first run: set up; afterwards: listen hands-free ("Alfred, …")
alfred setup        choose the server and enter the access token
alfred listen       hands-free voice (--push-to-talk, --barge-in, --input/--output-device)
alfred chat [text]  type to Alfred (one question, or a conversation)
alfred open         open Alfred's interface in an app window
alfred status       check the connection
alfred devices      list microphones and speakers
"""

from __future__ import annotations

import argparse
import getpass
import sys

from alfred_desktop import __version__
from alfred_desktop.chat import ServerError, describe, health, repl
from alfred_desktop.chat import ask as ask_once
from alfred_desktop.config import Config, config_path, normalize_server


def setup(cfg: Config) -> int:
    print("Alfred setup. Your server's address and access token were printed by")
    print("deploy/install.sh on the server (the token is ALFRED_API_TOKEN in /opt/alfred/.env).\n")
    while True:
        default = f" [{cfg.server}]" if cfg.server else ""
        server = normalize_server(input(f"Server address{default}: ") or cfg.server)
        if server:
            break
        print("  That doesn't look like an address, e.g. https://alfred.your-tailnet.ts.net")
    token = getpass.getpass("Access token" + (" [keep current]" if cfg.token else "") + ": ")
    cfg.server, cfg.token = server, (token.strip() or cfg.token)
    try:
        print("\nChecking…", describe(health(cfg)))
    except ServerError as exc:
        print(f"\nWarning: {exc}. Saving anyway; fix it with `alfred setup`.")
    if server.startswith("http://") and "localhost" not in server and "127.0.0.1" not in server:
        print("Note: plain http:// works here, but phones need the https:// address.")
    print(f"Saved to {cfg.save()}")
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="alfred", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", action="version", version=f"alfred {__version__}")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("setup", help="choose the server and enter the access token")
    lis = sub.add_parser("listen", help="hands-free voice")
    lis.add_argument("--push-to-talk", action="store_true", help="press Enter, then speak")
    lis.add_argument(
        "--barge-in",
        action="store_true",
        help="interrupt him by talking (needs an echo-cancelling speakerphone)",
    )
    lis.add_argument("--input-device", type=int)
    lis.add_argument("--output-device", type=int)
    lis.add_argument("--save", action="store_true", help="remember these options")
    lis.add_argument("--wav", help=argparse.SUPPRESS)
    lis.add_argument("--save-dir", default="alfred-replies", help=argparse.SUPPRESS)
    lis.add_argument("--linger", type=float, default=8.0, help=argparse.SUPPRESS)
    chat = sub.add_parser("chat", help="type to Alfred")
    chat.add_argument("message", nargs="*")
    sub.add_parser("open", help="open Alfred's interface in an app window")
    sub.add_parser("status", help="check the connection")
    sub.add_parser("devices", help="list microphones and speakers")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cfg = Config.load()
    command = args.command or "listen"

    if command == "setup" or (not cfg.ready and command != "devices"):
        if not sys.stdin.isatty() and not cfg.ready:
            print(
                f"Not set up yet: run `alfred setup` (settings file: {config_path()})",
                file=sys.stderr,
            )
            return 2
        setup(cfg)
        if command == "setup":
            return 0

    if command == "status":
        try:
            print(f"{cfg.server}: {describe(health(cfg))}")
            return 0
        except ServerError as exc:
            print(f"{cfg.server}: {exc}", file=sys.stderr)
            return 1
    if command == "devices":
        from alfred_desktop.voice import list_devices

        print(list_devices())
        return 0
    if command == "open":
        from alfred_desktop.window import open_app

        print(
            f"Opening Alfred in your {open_app(cfg.server)}. Enter the access token in its"
            " settings (gear) the first time."
        )
        return 0
    if command == "chat":
        try:
            if args.message:
                ask_once(cfg, " ".join(args.message), None)
                return 0
            return repl(cfg)
        except ServerError as exc:
            print(exc, file=sys.stderr)
            return 1

    # listen
    from alfred_desktop.voice import listen

    for name in ("push_to_talk", "barge_in"):
        if getattr(args, name, False):
            setattr(cfg, name, True)
    for name in ("input_device", "output_device"):
        if getattr(args, name, None) is not None:
            setattr(cfg, name, getattr(args, name))
    if getattr(args, "save", False):
        cfg.save()
    return listen(
        cfg,
        wav=getattr(args, "wav", None),
        save_dir=getattr(args, "save_dir", "alfred-replies"),
        linger=getattr(args, "linger", 8.0),
    )


if __name__ == "__main__":
    sys.exit(main())
