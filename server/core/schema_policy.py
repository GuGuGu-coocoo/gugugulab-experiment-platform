"""The supported Schema subset: no unbounded regular-expression rules.

Only schema-valued keywords are walked. A payload property named ``pattern``,
or data stored under const/enum/default/examples, is not itself a regex rule.
"""
from .protocol import require

SINGLE_SCHEMAS = ('not', 'if', 'then', 'else', 'items', 'contains', 'additionalItems',
                  'additionalProperties', 'unevaluatedProperties', 'unevaluatedItems', 'propertyNames',
                  'contentSchema')
SCHEMA_MAPS = ('properties', '$defs', 'definitions', 'dependentSchemas', 'dependencies')
SCHEMA_ARRAYS = ('allOf', 'anyOf', 'oneOf', 'prefixItems')


def reject_regex_rules(schema):
    pending = [schema]
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        require('pattern' not in node and 'patternProperties' not in node, 'schema_regex_unsupported')
        for key in SINGLE_SCHEMAS:
            child = node.get(key)
            if isinstance(child, dict):
                pending.append(child)
            elif isinstance(child, list):
                pending.extend(child)
        for key in SCHEMA_MAPS:
            children = node.get(key)
            if isinstance(children, dict):
                pending.extend(children.values())
        for key in SCHEMA_ARRAYS:
            children = node.get(key)
            if isinstance(children, list):
                pending.extend(children)
