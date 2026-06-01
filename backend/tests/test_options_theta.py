"""Black-Scholes put theta sign — regression for audit P0."""

from __future__ import annotations

from app.services.options import black_scholes_greeks


def test_atm_put_theta_greater_than_call_theta() -> None:
    """Put-call theta parity check.

    For ATM short-tenor European options without a dividend yield, both
    thetas are negative but put theta > call theta because the
    `r*K*e^(-rT)*N(d2)` term subtracts from call theta and adds to put
    theta. The earlier code subtracted both, mispricing protective puts.
    """
    call = black_scholes_greeks(S=100.0, K=100.0, T=0.5, sigma=0.2, r=0.05, option_type="call")
    put = black_scholes_greeks(S=100.0, K=100.0, T=0.5, sigma=0.2, r=0.05, option_type="put")
    assert call and put
    assert call["theta"] < 0
    assert put["theta"] > call["theta"]


def test_invalid_inputs_return_empty() -> None:
    assert black_scholes_greeks(0, 100, 0.5, 0.2) == {}
    assert black_scholes_greeks(100, 100, 0, 0.2) == {}
    assert black_scholes_greeks(100, 100, 0.5, 0) == {}
