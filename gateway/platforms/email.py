"""Compatibility shim for the bundled Email platform plugin.

The Email adapter lives in ``plugins.platforms.email.adapter``. Keep the legacy
``gateway.platforms.email`` import path working for older integrations and tests
that still import the adapter directly from ``gateway.platforms``.
"""

from plugins.platforms.email.adapter import EmailAdapter, check_email_requirements, register

__all__ = ["EmailAdapter", "check_email_requirements", "register"]
