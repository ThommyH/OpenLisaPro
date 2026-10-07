from lisa_pro_ui.pid import PIDController


def test_proportional_response():
    pid = PIDController(kp=35, ki=0, kd=0)
    out = pid.update(0.2, now=1.0)
    assert abs(out - 7.0) < 1e-9


def test_negative_error_lowers_output():
    pid = PIDController(kp=35, ki=0, kd=0)
    out = pid.update(-0.2, now=1.0)
    assert abs(out - (-7.0)) < 1e-9


def test_integral_accumulates():
    pid = PIDController(kp=0, ki=1.0, kd=0, integral_limit=100)
    pid.update(1.0, now=1.0)
    out = pid.update(1.0, now=2.0)
    assert out == 1.0  # integral += 1 * 1s
    out2 = pid.update(1.0, now=3.0)
    assert out2 == 2.0


def test_integral_limit():
    pid = PIDController(kp=0, ki=1.0, kd=0, integral_limit=5)
    pid.update(1.0, now=1.0)
    out = pid.update(1.0, now=20.0)  # dt=19 would push integral past limit
    assert abs(out - 5.0) < 1e-9
    assert abs(pid.integral - 5.0) < 1e-9


def test_deadband_holds_without_integral_grow():
    pid = PIDController(kp=10, ki=5, kd=0)
    pid.update(0.2, now=1.0)
    before = pid.integral
    pid.update(0.01, now=2.0, deadband=0.03)
    assert pid.integral == before


def test_reset_clears_state():
    pid = PIDController(kp=1, ki=1, kd=0)
    pid.update(1.0, now=1.0)
    pid.update(1.0, now=2.0)
    pid.reset()
    assert pid.integral == 0.0
    out = pid.update(1.0, now=10.0)
    assert out == 1.0  # no prior dt → no integral yet
