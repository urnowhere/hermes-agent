"""Embedded WhatsApp bridge (Node.js).

This package vendors the small Node.js daemon that talks to WhatsApp via
Baileys (``@whiskeysockets/baileys``).  It lives inside the ``gateway``
package so setuptools picks it up as package-data and ships it to
``site-packages/gateway/whatsapp_bridge/`` — which fixes #15336 (NixOS /
pip-installed builds previously had no copy on disk because
``scripts/whatsapp-bridge/`` was outside any Python package).

This site-packages location is a **read-only template**.  The WhatsApp
adapter copies these files to a writable directory under
``HERMES_HOME`` (``~/.hermes/whatsapp-bridge/``) on first use, then runs
``npm install`` there.  That two-step is mandatory because the Nix
store and many system pip installs are read-only — running
``npm install`` directly inside ``site-packages`` would fail.

The directory is intentionally a regular package (with this ``__init__``)
rather than a namespace package so package-data globs resolve cleanly
on every modern setuptools.  Nothing in here is meant to be imported
from Python — the consumer (``gateway.platforms.whatsapp``) launches
``bridge.js`` via ``subprocess`` from the runtime copy.
"""
