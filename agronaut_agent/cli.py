"""agronaut — one terminal command over everything the project ships.

Bare `agronaut` is the chat REPL, which is what most people want; the subcommands cover
the deterministic sizing engine, the two long-running services, and the maintainer tools.
Nothing here reimplements a feature — each command routes to the callable that already
owns it, so this module stays a dispatcher and only a dispatcher.

    agronaut                                        # chat with the agent
    agronaut size --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000
    agronaut design ...                             # alias for `size`
    agronaut web                                    # the Streamlit app
    agronaut bot                                    # the Telegram bot
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _root_importable() -> None:
    """`bot` and `app` are top-level modules at the project root, not package members."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _cmd_chat(args) -> int:
    from . import core  # imported late: `agronaut list` must not pay for the LLM stack
    core._repl()
    return 0


def _cmd_web(args) -> int:
    # sys.executable, not a bare "streamlit": the console script is often invoked by
    # absolute path (systemd, cron, another venv's shell) with our bin/ not on PATH.
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
                            *args.streamlit_args])


def _cmd_bot(args) -> int:
    _root_importable()
    import bot

    bot.main()
    return 0


def _cmd_review(args) -> int:
    from . import review

    review.main()
    return 0


def _cmd_analytics(args) -> int:
    from . import analytics

    return analytics.main()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agronaut",
        description="Aquaponics assistant: chat, computed sizing, web app, bot.",
        epilog="Run with no arguments to start chatting.",
    )
    p.set_defaults(func=_cmd_chat)  # bare `agronaut` -> chat
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("chat", help="chat with the agent in the terminal").set_defaults(func=_cmd_chat)

    # Sizing commands are defined once, in the portable skill CLI, and borrowed here under
    # shorter names so the two surfaces cannot drift apart.
    from skills.aquaponics_engineer.cli import add_sizing_subcommands

    add_sizing_subcommands(sub, aliases={"size-aquaponics": ["size", "design"],
                                         "size-hydroponics": ["size-hydro"]})

    web = sub.add_parser("web", help="run the Streamlit web app "
                                     "(trailing flags are passed to streamlit)")
    web.add_argument("streamlit_args", nargs="*",
                     help="e.g. --server.port=9000 --server.headless=true")
    web.set_defaults(func=_cmd_web)
    sub.add_parser("bot", help="run the Telegram bot").set_defaults(func=_cmd_bot)
    sub.add_parser("review", help="approve/reject pending community insights").set_defaults(
        func=_cmd_review)
    sub.add_parser("analytics", help="summarise recorded usage").set_defaults(func=_cmd_analytics)
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    # `web` is a launcher, so everything after it belongs to Streamlit and is handed over
    # verbatim: `--server.port=9000` is not ours to parse, and rejecting it would make this
    # command strictly worse than the `streamlit run` it replaces.
    if argv[:1] == ["web"] and argv[1:] not in ([], ["-h"], ["--help"]):
        args = parser.parse_args(["web"])
        args.streamlit_args = argv[1:]
        return args.func(args)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
