"""Synthetic tests for toolcheck.py (px4-log-health battery-r-2026, tool check). They run before
the plan gate and print no simulation result. By MV2 of Munim, Inc.; written with AI agents.
Apache-2.0."""

import math
import unittest

import numpy as np

import replay as rp
import toolcheck as tc


def ref_rls_update(theta, P, x, V, I, lam):
    """A hand-written copy of the update in PX4's int_res_est_replay.py (as read on 2026-11-12),
    used only to test this file's wrappers; the study itself runs the downloaded function."""
    gamma = P @ x / (lam + x.T @ P @ x)
    error = V - x.T @ theta
    data_cov = x.T @ P @ x
    theta_temp = theta + gamma * error
    P_temp = (P - gamma @ x.T @ P) / lam
    if (abs(np.linalg.norm(P)) < abs(np.linalg.norm(P_temp))):
        theta_corr = np.array([V + theta[1] * I, theta[1]])
        P_corr = P
        return theta_corr, P_corr, error, data_cov, 0, 0
    return theta_temp, P_temp, error, data_cov, gamma[0], gamma[1]


def synthetic(n=900):
    """100 Hz samples: idle with small ripple, a few zero and negative currents, spool, flight."""
    k = np.arange(n)
    t = k.astype(np.int64) * 10000
    i = np.where(k < 400, 0.8 + 0.05 * np.sin(0.21 * k), np.where(k < 500, 3.0, 15.0 + 4.0 * np.sin(0.013 * k)))
    i[250] = 0.0
    i[251] = -0.1
    v = 4.0 * (4.0 - 0.0027 * i) + 0.01 * np.sin(0.37 * k)
    return t, v, i


class LoadTests(unittest.TestCase):
    def test_only_the_function_runs(self):
        src = ("import sys\nraise SystemExit('top level ran')\n\n"
               "def rls_update(theta, P, x, V, I, lam):\n    return np.zeros(2) + lam\n\nprint('x')\n")
        f = tc.load_rls_update(src)
        self.assertEqual(list(f(None, None, None, None, None, 1.5)), [1.5, 1.5])

    def test_missing_function(self):
        with self.assertRaises(ValueError):
            tc.load_rls_update("def other():\n    return 1\n")


TOOL = ("    P = np.diag([1.2 * n_cells, 0.1 * n_cells]) # Initial covariance\n"
        "        internal_resistance_stable[index] = max(R_est[index]/n_cells, 0.001)\n"
        "    parser.add_argument('-l', type = float, required = False, default = 0.99, help = 'Forgetting factor')\n")
BAT_H = ("\tstatic constexpr float LAMBDA = 0.95f; \t// [0, 1] Forgetting factor\n"
         "\tstatic constexpr float OCV_COVARIANCE = 1.5f; // Initial per cell covariance\n")
BAT_CPP = ("\t\t_internal_resistance_estimate =\n"
           "\t\t\tmath::max(_RLS_est(1) / _params.n_cells, 0.f); // Only use positive values\n"
           "\tif (current_a > FLT_EPSILON) {\n")


class FactTests(unittest.TestCase):
    def test_all_found(self):
        graded, reported = tc.code_facts(TOOL, BAT_H, BAT_CPP)
        self.assertEqual(len(graded), 6)
        self.assertTrue(all(graded.values()), graded)
        self.assertTrue(reported["tool_no_current_condition"])
        self.assertTrue(reported["fw_update_if_current_positive"])

    def test_changed_values_not_found(self):
        graded, _ = tc.code_facts(TOOL.replace("default = 0.99", "default = 0.95"), BAT_H.replace("1.5f", "1.2f"),
                                  BAT_CPP.replace("0.f)", "0.001f)"))
        self.assertFalse(graded["tool_lambda_default_0.99"])
        self.assertFalse(graded["fw_ocv_covariance_1.5"])
        self.assertFalse(graded["fw_published_floor_0"])
        self.assertTrue(graded["tool_shown_floor_0.001"])

    def test_current_condition_in_tool_is_seen(self):
        _, reported = tc.code_facts(TOOL + "        if I[index] > 0:\n", BAT_H, BAT_CPP)
        self.assertFalse(reported["tool_no_current_condition"])


class WrapperTests(unittest.TestCase):
    def test_firmware_pattern_matches_port_float64(self):
        t, v, i = synthetic()
        pub, raw, acc = tc.tool_firmware_pattern(ref_rls_update, t, v, i, 4)
        r = rp.replay(t, v, i, np.ones(len(t), dtype=bool), 4, gated=True, dtype=np.float64)
        same, d = tc.parity(acc, r["accepted"], pub, r["r_pub"])
        self.assertTrue(same)
        self.assertLessEqual(d, tc.PARITY_TOL)
        self.assertFalse(acc[250] or acc[251])          # no update at current <= FLT_EPSILON
        self.assertGreater(int(np.sum(acc)), 0)
        self.assertTrue(np.all(np.isfinite(raw)))

    def test_shipped_constant_input_does_not_move(self):
        # 4 cells, R 5 mOhm per cell: V = 16.0 - 0.02 * 1.0; the tool starts at OCV 15.98 + 0.02 = 16.0,
        # R 0.02, so the prediction error is 0 on every sample and R stays 0.005 per cell
        n = 50
        v = np.full(n, 15.98)
        i = np.full(n, 1.0)
        raw, acc = tc.tool_shipped(ref_rls_update, v, i, 4)
        self.assertTrue(np.all(np.abs(raw - 0.005) < 1e-12), raw[:5])
        self.assertEqual(len(acc), n)

    def test_shipped_shows_the_floor(self):
        def fake(theta, P, x, V, I, lam):
            return np.array([[V], [-0.01]]), P * 0.5, 0, 0, 0, 0
        raw, acc = tc.tool_shipped(fake, np.full(3, 16.0), np.full(3, 2.0), 4)
        self.assertTrue(np.all(acc))
        self.assertTrue(np.allclose(raw, -0.0025))
        self.assertTrue(np.allclose(tc.shown(raw), 0.001))

    def test_shipped_reject_is_seen(self):
        def fake(theta, P, x, V, I, lam):
            return np.array([V + theta[1] * I, theta[1]]), P, 0, 0, 0, 0
        raw, acc = tc.tool_shipped(fake, np.full(4, 16.0), np.full(4, 2.0), 4)
        self.assertFalse(np.any(acc))
        self.assertTrue(np.allclose(raw, 0.005))

    def test_shipped_updates_at_zero_current(self):
        seen = []

        def fake(theta, P, x, V, I, lam):
            seen.append((I, lam))
            return theta, P, 0, 0, 0, 0
        tc.tool_shipped(fake, np.full(3, 16.0), np.array([0.0, -0.2, 1.0]), 4)
        self.assertEqual(seen, [(0.0, 0.99), (-0.2, 0.99), (1.0, 0.99)])

    def test_firmware_pattern_resets_for_two_seconds(self):
        calls = []

        def fake(theta, P, x, V, I, lam):
            calls.append((float(theta[1][0]), float(P[0][0]), lam))
            return np.array([[V], [-0.04]]), P * 0.5, 0, 0, 0, 0
        t = np.array([0, 1000000, 2000000, 2000001, 3000000], dtype=np.int64)
        pub, raw, acc = tc.tool_firmware_pattern(fake, t, np.full(5, 16.0), np.full(5, 1.0), 4)
        # samples 0-2 (t <= 2 s) start from the reset state; samples 3-4 continue from the update
        self.assertEqual([c[0] for c in calls[:3]], [0.02, 0.02, 0.02])
        self.assertEqual([c[1] for c in calls[:3]], [6.0, 6.0, 6.0])
        self.assertEqual(calls[3][0], -0.04)
        self.assertEqual(calls[3][1], 3.0)
        self.assertEqual(calls[0][2], 0.95)
        self.assertTrue(np.all(acc))
        self.assertTrue(np.all(pub == 0.0))
        self.assertTrue(np.allclose(raw, -0.01))


class HelperTests(unittest.TestCase):
    def test_parity(self):
        self.assertEqual(tc.parity([True, False], [True, False], [0.001, 0.002], [0.001, 0.002]), (True, 0.0))
        same, d = tc.parity([True, True], [True, False], [0.001, 0.002], [0.001, 0.0025])
        self.assertFalse(same)
        self.assertTrue(math.isclose(d, 0.0005, rel_tol=1e-9))

    def test_air_share(self):
        acc = np.array([[True, True], [False, True], [True, False], [False, False]])
        self.assertEqual(list(tc.air_share(acc, 2)), [0.5, 0.0])

    def test_predictions_thresholds(self):
        graded = {k: True for k in tc.GRADED_FACTS}
        s, takeoff, n = 10, 2, 6
        conf = {}
        for name in tc.CONFIGS:
            conf[name] = {"pub": np.full((n, s), 0.003), "raw": np.full((n, s), 0.003),
                          "acc": np.zeros((n, s), dtype=bool)}
        conf["T0"]["acc"][takeoff:, :9] = True       # T0 accepts more in 9 of 10 runs
        conf["T1"]["pub"][:, 0] = 0.004              # one run out of parity
        pred, rep, per_run = tc.summarise(conf, takeoff, graded, {})
        self.assertTrue(pred["C1"])
        self.assertFalse(pred["P1"])                 # 9 of 10 < 99%
        self.assertTrue(pred["P2"])                  # 9 of 10 >= 90%
        self.assertEqual(rep["p1_runs_in_parity"], 9)
        self.assertEqual(rep["p2_runs_T0_accepts_more_in_air"], 9)
        self.assertEqual(len(per_run), s)
        graded["fw_lambda_0.95"] = False
        self.assertFalse(tc.summarise(conf, takeoff, graded, {})[0]["C1"])


if __name__ == "__main__":
    unittest.main()
