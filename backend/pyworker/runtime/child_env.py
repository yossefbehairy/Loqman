"""Minimal environment for CLI children; credentials remain in profile files."""
import os

_NAMES = frozenset("PATH SYSTEMROOT WINDIR COMSPEC PATHEXT TEMP TMP USERPROFILE HOMEDRIVE HOMEPATH APPDATA LOCALAPPDATA PROGRAMDATA NUMBER_OF_PROCESSORS PROCESSOR_ARCHITECTURE OS LANG HOME CODEX_HOME".split())


def child_env(api_key_names=()):
    """Allowlisted environment.

    ``api_key_names`` are passed through only when the caller says the user opted
    in to API-key mode; everything else (including AGENTOS_LOCAL_TOKEN) is dropped.
    """
    extra = {n.upper() for n in api_key_names}
    return {k: v for k, v in os.environ.items()
            if k.upper() in _NAMES or k.upper() in extra or k.upper().startswith(("PROGRAMFILES", "LC_"))}
