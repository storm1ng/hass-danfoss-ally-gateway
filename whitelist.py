"""Whitelist for vulture — suppress false positives.

Home Assistant discovers these entry points dynamically, so vulture
will report them as unused even though they are required.
"""

# Integration setup / teardown (called by HA core)
async_setup_entry  # noqa
async_unload_entry  # noqa

# Config flow
DanfossAllyGatewayConfigFlow  # noqa
