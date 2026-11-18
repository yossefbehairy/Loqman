"""Minimal environment for CLI children; credentials remain in profile files."""
import os

_NAMES = frozenset("PATH SYSTEMROOT WINDIR COMSPEC PATHEXT TEMP TMP USERPROFILE HOMEDRIVE HOMEPATH APPDATA LOCALAPPDATA PROGRAMDATA PROGRAMFILES PROGRAMFILES(X86) PROGRAMW6432 NUMBER_OF_PROCESSORS PROCESSOR_ARCHITECTURE OS LANG LANGUAGE LC_ALL LC_CTYPE LC_MESSAGES LC_TIME HOME CODEX_HOME HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE NODE_EXTRA_CA_CERTS GOOGLE_APPLICATION_CREDENTIALS".split())


def child_env(api_key_names=()):
    """Allowlisted environment.

    ``api_key_names`` are passed through only when the caller says the user opted
    in to API-key mode; everything else (including AGENTOS_LOCAL_TOKEN) is dropped.
    """
    extra = {n.upper() for n in api_key_names}
    return {k: v for k, v in os.environ.items()
            if k.upper() in _NAMES or k.upper() in extra}
