import pytest
from core.protocol import parse, equal, Rejected

@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'NaN', b'-0.0', b'9007199254740992', b'['*18+b'0'+b']'*18])
def test_reject_ambiguous_values(raw):
    with pytest.raises(Rejected):
        parse(raw)

def test_strict_value_comparison():
    assert not equal({'x': False}, {'x': 0})
    assert not equal({'x': None}, {})
    assert equal({'a': 1, 'b': ['001', None]}, {'b': ['001', None], 'a': 1.0})
    assert not equal([1,2], [2,1])
