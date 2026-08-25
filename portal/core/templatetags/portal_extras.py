"""
Small template filters the portal's templates need but Django doesn't ship.
"""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def get_item(mapping: dict, key):
    """`{{ some_dict|get_item:some_variable_key }}` — Django's dot lookup can't
    take a variable key, only a literal attribute/index name."""
    if mapping is None:
        return None
    return mapping.get(key)
