"""Inventory-backed Helm release observation and command domain.

The domain never decodes release Secret data. Mutations are exposed only when
an authorized target Agent advertises an existing executable capability.
"""
