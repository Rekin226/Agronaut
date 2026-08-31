"""Local admin CLI to review community-insight nominations — the ONLY place the privileged
"promote to everyone" action happens. Off the chat surface: no Telegram message can trigger it.

Run:  python -m agronaut_agent.review
"""

from __future__ import annotations

from .store import CommunityStore, _Db


def format_candidate(c: dict) -> str:
    """Render one pending candidate for the owner's review (shows the private context)."""
    return (
        f"[{c['id']}] topic: {c.get('topic') or '—'}\n"
        f"    from:     {c['source_user_id']}\n"
        f"    original: {c.get('original') or '—'}\n"
        f"    SHARE AS: {c['insight']}"
    )


def apply_command(store: CommunityStore, cmd: str) -> str:
    """Parse and apply one review command over `store`. Returns a message; '__quit__' to exit."""
    parts = (cmd or "").strip().split()
    if not parts:
        return ""
    verb = parts[0].lower()
    if verb in ("quit", "exit", "q"):
        return "__quit__"
    if verb in ("approve", "reject") and len(parts) == 2 and parts[1].isdigit():
        cid = int(parts[1])
        if cid not in {c["id"] for c in store.pending()}:
            return f"No pending candidate with id {cid}."
        (store.approve if verb == "approve" else store.reject)(cid)
        return f"{'Approved' if verb == 'approve' else 'Rejected'} #{cid}."
    return "Commands: approve <id> | reject <id> | quit"


def main() -> None:  # pragma: no cover (interactive loop)
    store = CommunityStore(_Db())
    print("Community insight review — approve <id> / reject <id> / quit")
    while True:
        pending = store.pending()
        if not pending:
            print("No pending candidates. Done.")
            return
        print(f"\n{len(pending)} pending:\n")
        for c in pending:
            print(format_candidate(c) + "\n")
        try:
            cmd = input("review> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        msg = apply_command(store, cmd)
        if msg == "__quit__":
            return
        if msg:
            print(msg)


if __name__ == "__main__":  # pragma: no cover
    main()
