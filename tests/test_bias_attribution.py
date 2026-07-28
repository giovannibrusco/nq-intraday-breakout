from tools.bias_attribution import published_endpoint_checks


def _published_endpoints():
    first = {'trades': 1728, 'net_pnl': 3287392.0, 'sharpe': 0.95}
    last = {'trades': 1725, 'net_pnl': 2800810.0, 'sharpe': 1.02}
    return first, last


def test_published_endpoint_checks_accept_exact_reproduction():
    first, last = _published_endpoints()
    assert all(matches for _, _, _, matches in published_endpoint_checks(first, last))


def test_published_endpoint_checks_detect_mismatch():
    first, last = _published_endpoints()
    last['trades'] += 1
    failed = [
        name
        for name, _, _, matches in published_endpoint_checks(first, last)
        if not matches
    ]
    assert failed == ['corrected trades']
