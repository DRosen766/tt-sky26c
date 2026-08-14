from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: write a PNG, never try to open a window
import matplotlib.pyplot as plt
import numpy as np

# Simulation parameters
DT = 1.0  # ms per timestep
N_STEPS = 100  # timesteps (1000 x 1 ms = 1 s)
TAU = 2.0  # ms
I_PEAK = 255.0 / 256.0  # Q0.8, normalized to [0, 1)
RATE_HZ = 1.0  # presynaptic Poisson rate
SEED = 10  # fixed so the trace is reproducible


def alpha_current(t, t_spike, tau, I_peak):
    """
    Alpha-function synaptic current.

    Parameters
    ----------
    t : ndarray or float
        Current simulation time.
    t_spike : float
        Presynaptic spike time.
    tau : float
        Time constant.
    I_peak : float
        Peak current.

    Returns
    -------
    Current at time t.
    """
    x = (t - t_spike) / tau
    return np.where(x >= 0, I_peak * x * np.exp(1 - x), 0.0)


def poisson_spikes(n_steps, dt, rate_hz, rng):
    """Bernoulli approximation to a Poisson process: one spike per step at most."""
    # p = rate_hz * (dt / 1000.0)  # dt is in ms, rate in Hz
    # return rng.random(n_steps) < p
    spk_arr = np.zeros(n_steps)
    spk_arr[10] = 1
    return spk_arr.astype(bool)


def alpha_trace(t, spike_times, tau, I_peak):
    """Superposition of alpha currents from every spike in spike_times."""
    if len(spike_times) == 0:
        return np.zeros_like(t)
    # (n_spikes, n_steps) -> sum over spikes
    return alpha_current(t[None, :], spike_times[:, None], tau, I_peak).sum(axis=0)


rng = np.random.default_rng(SEED)
t = np.arange(N_STEPS) * DT
spikes = poisson_spikes(N_STEPS, DT, RATE_HZ, rng)
spike_times = t[spikes]
I = alpha_trace(t, spike_times, TAU, I_PEAK)

print(f"dt={DT} ms  steps={N_STEPS}  tau={TAU} ms  I_peak={I_PEAK}  rate={RATE_HZ} Hz  seed={SEED}")
print(f"{len(spike_times)} spikes at t = {', '.join(f'{ts:g}' for ts in spike_times)}")
print(f"peak I = {I.max():.6f} at t = {t[I.argmax()]:g} ms")


def plot_trace(t, I, spike_times, path):
    fig, ax = plt.subplots(figsize=(9, 3.5), constrained_layout=True)

    # Spikes first so the current sits on top of them.
    ax.vlines(
        spike_times,
        0,
        I.max() * 1.05,
        color="#9aa0a6",
        linewidth=1.0,
        label="presynaptic spike",
    )
    ax.plot(t, I, color="#1f6feb", linewidth=2.0, label="I(t)")

    ax.set_xlabel("t (ms)")
    ax.set_ylabel("I")
    ax.set_title(
        f"Alpha-function synaptic current, Poisson input "
        f"({RATE_HZ:g} Hz, tau={TAU:g} ms, dt={DT:g} ms, {N_STEPS} steps)"
    )
    ax.set_xlim(t[0], t[-1])
    ax.set_ylim(0, I.max() * 1.15)
    ax.grid(True, color="#e5e7eb", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(frameon=False, loc="upper right")

    fig.savefig(path, dpi=150)
    plt.close(fig)


out = Path(__file__).parent / "alpha_trace.png"
plot_trace(t, I, spike_times, out)
print(f"plot written to {out}")
