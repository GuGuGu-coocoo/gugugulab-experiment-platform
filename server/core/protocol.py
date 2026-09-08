"""GEP/1 bounded JSON values; comparison preserves JSON types."""
import json
import math
import uuid

PROTOCOL = 'gep/1'
MAX_BYTES = 262144
MAX_BATCH = 64
MAX_EVENTS = 10000

class Rejected(Exception):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)

def require(condition, code, status=422):
    if not condition:
        raise Rejected(code, status)

def uuid_text(value):
    require(isinstance(value, str), 'invalid_uuid')
    try:
        require(str(uuid.UUID(value)) == value, 'invalid_uuid')
    except ValueError:
        raise Rejected('invalid_uuid')
    return value

def validate_tree(value, depth=0):
    require(depth <= 16, 'json_depth')
    if value is None or type(value) is bool:
        return
    if type(value) in (int, float):
        require(math.isfinite(value) and abs(value) <= 9007199254740991, 'number_range')
        require(not (isinstance(value, float) and value == 0 and math.copysign(1, value) < 0), 'negative_zero')
    elif isinstance(value, str):
        require(len(value) <= 16384, 'string_limit')
    elif isinstance(value, list):
        require(len(value) <= MAX_EVENTS, 'array_limit')
        for item in value:
            validate_tree(item, depth + 1)
    elif isinstance(value, dict):
        require(len(value) <= 128, 'object_limit')
        for key, item in value.items():
            require(isinstance(key, str) and len(key) <= 128, 'key_limit')
            validate_tree(item, depth + 1)
    else:
        raise Rejected('json_type')

def parse(raw):
    require(len(raw) <= MAX_BYTES, 'body_limit', 413)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_key')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise Rejected('invalid_json')
    validate_tree(value)
    return value

def equal(a, b):
    if type(a) is bool or type(b) is bool:
        return type(a) is type(b) and a == b
    if type(a) in (int, float) and type(b) in (int, float):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b
