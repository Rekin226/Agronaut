"""The Telegram adapter's command wiring — verified without running the bot."""

from agronaut_agent.channels.telegram_adapter import TelegramAdapter


def _adapter():
    # token bypasses env lookup; allowed_ids=[] -> open (we only inspect wiring here).
    return TelegramAdapter(agent=object(), token="x:y", allowed_ids=[])


def test_command_specs_include_mode_commands():
    names = [c for c, _h, _desc in _adapter()._command_specs()]
    for cmd in ("design", "optimize", "troubleshoot"):
        assert cmd in names


def test_command_specs_keep_existing_commands_for_menu():
    names = [c for c, _h, _desc in _adapter()._command_specs()]
    for cmd in ("start", "help", "whoami", "reset", "forget"):
        assert cmd in names


def test_every_command_spec_has_a_callable_handler_and_description():
    for cmd, handler, desc in _adapter()._command_specs():
        assert callable(handler), cmd
        assert isinstance(desc, str) and desc, cmd


def test_mode_handlers_exist():
    a = _adapter()
    for attr in ("_on_design", "_on_optimize", "_on_troubleshoot", "_set_mode", "_post_init"):
        assert hasattr(a, attr)
