"""Short-horizon load forecasting: rolling least-squares linear trend with a
regression prediction interval.

For each zone we fit load = a + b*t over the last W samples. The forecast at
horizon h is the fitted line extended by h seconds; the upper band is

    forecast + k * s * sqrt(1 + 1/N + (t_h - t_mean)^2 / Sxx)

the textbook prediction interval for a linear regression, so uncertainty grows
automatically the further we extrapolate and the noisier the zone is.

Why this and not ML: the horizon is short (tens of seconds), the signal is
trend-dominated, there is little history at demo start, and every number can be
explained in one sentence. (Holt smoothing was tried first; its trend estimate
was ~5x noisier on the same data, producing phantom risks at a 45 s horizon.)

v0.7 adds AdaptiveForecaster (default): an online-weighted ensemble of three trend
windows and the J-1 plan shape, with a conformally calibrated band. See its docstring.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Forecast:
    level: float          # fitted load now, kW
    trend: float          # kW per second
    sigma: float          # residual std, kW
    n: int
    ready: bool
    t_offset: float = 0.0 # now minus the mean sample time, s
    sxx: float = 1.0
    info: Optional[dict] = None   # adaptive model: expert weights, calibration factor

    def at(self, h: float) -> float:
        return max(0.0, self.level + self.trend * h)

    def upper(self, h: float, k: float) -> float:
        if self.n < 3:
            return self.at(h)
        d = self.t_offset + h
        return self.at(h) + k * self.sigma * math.sqrt(1.0 + 1.0 / self.n + d * d / self.sxx)

    def peak_upper(self, h: float, k: float) -> float:
        """Worst case over [0, h]: linear forecast, so the max is at an end."""
        return max(self.upper(0.0, k), self.upper(h, k))

    def std(self, h: float) -> float:
        """Standard deviation of the prediction h seconds ahead (regression prediction interval)."""
        if self.n < 3:
            return 0.0
        d = self.t_offset + h
        return self.sigma * math.sqrt(1.0 + 1.0 / self.n + d * d / self.sxx)

    def peak(self, h: float) -> tuple[float, float]:
        """(mean, std) of the highest load expected in [0, h]: the line's end that is higher."""
        return (self.at(h), self.std(h)) if self.trend >= 0 else (self.at(0.0), self.std(0.0))


class TrendForecaster:
    def __init__(self, window: int, warmup: int):
        self.window, self.warmup = window, warmup
        self.samples: deque[tuple[float, float]] = deque(maxlen=window)
        self.n_total = 0
        self.mean = 0.0     # long-run mean, the baseline for capacity-blind (L0) zones

    def update(self, t: float, x: float) -> None:
        self.samples.append((t, x))
        self.n_total += 1
        self.mean += (x - self.mean) / min(self.n_total, 300)

    def rebase(self, x: float) -> None:
        """Known step change (a switch operated): the fit continues from x.

        Load transfers are proportional (a tie moves a fraction of demand), so a zone left with r = x / level of
        its load also keeps r of its trend. History is rescaled around the current level: v' = x + r (v - level).
        (v0.6 kept the full slope, so a relieved zone still looked as if it ramped at the pre-switch rate.)"""
        if not self.samples:
            return
        level = self.snapshot().level
        r = min(3.0, max(0.2, x / level)) if level > 1e-6 and x > 0 else 1.0
        self.samples = deque(((t, x + r * (v - level)) for t, v in self.samples), maxlen=self.window)

    def reset(self) -> None:
        self.samples.clear()
        self.n_total = 0
        self.mean = 0.0

    def restart(self, x: float) -> None:
        """Regime change confirmed (a real, sudden jump): the old trend no longer applies. History is replaced by a
        flat line at x on the same timestamps, so the forecaster stays ready and builds the new trend from here."""
        if not self.samples:
            return
        self.samples = deque(((t, x) for t, _ in self.samples), maxlen=self.window)

    def snapshot(self) -> Forecast:
        n = len(self.samples)
        if n == 0:
            return Forecast(0.0, 0.0, 0.0, 0, False)
        ts = [t for t, _ in self.samples]
        xs = [x for _, x in self.samples]
        t_now = ts[-1]
        if n < 3:
            return Forecast(xs[-1], 0.0, 0.0, n, False)
        tm, xm = sum(ts) / n, sum(xs) / n
        sxx = sum((t - tm) ** 2 for t in ts) or 1e-9
        b = sum((t - tm) * (x - xm) for t, x in zip(ts, xs)) / sxx
        a = xm - b * tm
        resid = [x - (a + b * t) for t, x in zip(ts, xs)]
        s = math.sqrt(sum(r * r for r in resid) / max(n - 2, 1))
        return Forecast(level=a + b * t_now, trend=b, sigma=s, n=n, ready=n >= self.warmup,
                        t_offset=t_now - tm, sxx=sxx)


# ====================================================================== adaptive, calibrated forecaster
def _fit(samples) -> Optional[Forecast]:
    n = len(samples)
    if n < 3:
        return None
    ts = [t for t, _ in samples]
    xs = [x for _, x in samples]
    tm, xm = sum(ts) / n, sum(xs) / n
    sxx = sum((t - tm) ** 2 for t in ts) or 1e-9
    b = sum((t - tm) * (x - xm) for t, x in zip(ts, xs)) / sxx
    a = xm - b * tm
    s = math.sqrt(sum((x - (a + b * t)) ** 2 for t, x in zip(ts, xs)) / max(n - 2, 1))
    return Forecast(level=a + b * ts[-1], trend=b, sigma=s, n=n, ready=True, t_offset=ts[-1] - tm, sxx=sxx)


class AdaptiveForecaster(TrendForecaster):
    """A small, explainable ensemble that keeps score of itself.

    Experts, each predicting the load `horizon` seconds ahead:
      fast    linear trend over the last `windows[0]` samples (reacts to ramps quickly)
      base    linear trend over `windows[1]` samples (the v0.6 forecaster)
      slow    linear trend over `windows[2]` samples (steady in noise)
      plan    current level + the change the J-1 plan expects over the horizon (knows the daily shape)

    Every prediction is checked when its target time arrives. Each expert's squared
    error is tracked with an exponential forgetting factor; the combined forecast
    weights experts by inverse recent error (inverse-variance weighting). Nothing
    is trained offline: weights adapt online, per zone, from that zone's own data.

    Calibration (split-conformal style): the realized errors of the combined
    forecast, divided by the regression's own predicted std, are kept for the last
    `calibration_samples` checks. The band is widened or narrowed by the factor
    that makes the empirical 97.7% quantile match the nominal 2-sigma point, so
    "upper band" and P(overload) mean what they say. Factor clamped to [0.75, 5].

    A switch operation rebases every window (as before) and drops predictions
    that straddle the switch, so the Brain's own actions never count as errors.
    """

    K_REF = 2.0               # calibrate the 2-sigma one-sided point (97.7 %)
    Q_REF = 0.97725

    def __init__(self, window: int, warmup: int, horizon: float, windows: tuple[int, int, int] = (10, 20, 40),
                 half_life: float = 60.0, calibration_samples: int = 200, min_checks: int = 20,
                 plan_fn: Optional[Callable[[float], Optional[float]]] = None):
        super().__init__(max(windows), warmup)
        self.windows = {"fast": windows[0], "base": windows[1], "slow": windows[2]}
        self.horizon = horizon
        self.decay = 0.5 ** (1.0 / half_life)
        self.mse: dict[str, float] = {}
        self.checks: dict[str, int] = {}
        self.calib: deque[float] = deque(maxlen=calibration_samples)
        self.min_checks = min_checks
        self.pending: deque[tuple[float, dict[str, float], float, float]] = deque()
        self.plan_fn = plan_fn
        self._cache: Optional[Forecast] = None

    # ------------------------------------------------------------------ experts
    def _experts(self) -> tuple[Optional[Forecast], dict[str, float]]:
        smp = list(self.samples)
        base = _fit(smp[-self.windows["base"]:])
        if base is None:
            return None, {}
        out = {"base": base.at(self.horizon)}
        for name in ("fast", "slow"):
            if len(smp) >= min(self.windows[name], 5):
                f = _fit(smp[-self.windows[name]:])
                if f:
                    out[name] = f.at(self.horizon)
        if self.plan_fn:
            t_now = smp[-1][0]
            p0, p1 = self.plan_fn(t_now), self.plan_fn(t_now + self.horizon)
            if p0 is not None and p1 is not None:
                out["plan"] = max(0.0, base.level + (p1 - p0))
        return base, out

    def weights(self, names) -> dict[str, float]:
        ready = [n for n in names if self.checks.get(n, 0) >= self.min_checks]
        if "base" not in ready:
            return {"base": 1.0}
        inv = {n: 1.0 / (self.mse[n] + 1e-6) for n in ready}
        tot = sum(inv.values())
        return {n: v / tot for n, v in inv.items()}

    def calibration(self) -> float:
        if len(self.calib) < self.min_checks:
            return 1.0
        q = sorted(self.calib)[min(len(self.calib) - 1, int(self.Q_REF * len(self.calib)))]
        return min(5.0, max(0.75, q / self.K_REF))

    # ------------------------------------------------------------------ learning loop
    def update(self, t: float, x: float) -> None:
        super().update(t, x)
        self._cache = None
        while self.pending and self.pending[0][0] + self.horizon <= t + 0.5:
            t_issued, preds, combined, std_raw = self.pending.popleft()
            if t_issued + self.horizon < t - 0.5:
                continue                      # gap in data: target sample missed
            for n, p in preds.items():
                e2 = (x - p) ** 2
                self.mse[n] = e2 if n not in self.mse else self.decay * self.mse[n] + (1 - self.decay) * e2
                self.checks[n] = self.checks.get(n, 0) + 1
            if std_raw > 0:
                self.calib.append((x - combined) / std_raw)
        base, preds = self._experts()
        if base is not None and len(self.samples) >= self.warmup:
            w = self.weights(preds)
            combined = sum(w[n] * preds[n] for n in w)
            self.pending.append((t, preds, combined, base.std(self.horizon)))

    def rebase(self, x: float) -> None:
        super().rebase(x)
        self.pending.clear()
        self._cache = None

    def reset(self) -> None:
        super().reset()
        self.pending.clear()
        self._cache = None

    def restart(self, x: float) -> None:
        super().restart(x)
        self.pending.clear()
        self._cache = None

    # ------------------------------------------------------------------ output
    def snapshot(self) -> Forecast:
        if self._cache is not None:
            return self._cache
        n = len(self.samples)
        if n < 3:
            return super().snapshot()
        base, preds = self._experts()
        w = self.weights(preds)
        combined = max(0.0, sum(w[k] * preds[k] for k in w))
        c = self.calibration()
        fc = Forecast(level=base.level, trend=(combined - base.level) / self.horizon, sigma=base.sigma * c,
                      n=base.n, ready=n >= self.warmup, t_offset=base.t_offset, sxx=base.sxx)
        fc.info = {"weights": {k: round(v, 2) for k, v in w.items()}, "calibration": round(c, 2),
                   "experts_kw": {k: round(v, 1) for k, v in preds.items()}}
        self._cache = fc
        return fc
