from tools.bias_attribution import STEPS, build_state, published_endpoint_checks


def _published_endpoints():
    first = {'trades': 1728, 'net_pnl': 3287392.0, 'sharpe': 0.95}
    last = {'trades': 1724, 'net_pnl': 2744225.0, 'sharpe': 1.00}
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


def test_only_the_legacy_row_reads_bars_as_start_stamped():
    # The legacy row reproduces the original notebook, which read each
    # timestamp as the start of its bar; every later row uses bar end.
    stamps = [build_state(i, 0.01)[3] for i in range(len(STEPS) + 1)]
    assert stamps == ['start'] + ['end'] * len(STEPS)
