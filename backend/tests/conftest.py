"""Shared test setup: never touch a developer's real Keychain / Credential Manager."""
import os

os.environ["PW_DISABLE_KEYRING"] = "1"
