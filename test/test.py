# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, NextTimeStep, ReadOnly

OUTPUT_DIR = Path(__file__).parent / "output"  # gitignored scratch for artifacts


OVERALL_FREQUENCY = 1000  # Hz
NUM_STEPS = 100  # total clock cycles to simulate, reset included
RESET_CYCLES = 1  # cycles rst_n is held low, counted against NUM_STEPS

VOLTAGE_CLAMP_BIT = 6  # ui_in[6] is the voltage clamp input
SPIKE_BIT = 7  # ui_in[7] is the pre-synaptic spike input
t_spike = 10
t_dur = 10  # cycles the pre-synaptic spike is held high


# Chart palette (light surface). One series, so no cycling.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
SERIES_I = "#2a78d6"
SPIKE_BAND = "#e8e8e4"


def plot_i(steps, i_raw, spikes, path, title):
    """Save a trace of synaptic current I_syn vs. cycle. No-op if matplotlib is unavailable."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless: no display in CI or the devcontainer
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, ax = plt.subplots(figsize=(8, 4), dpi=140)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    # Shade the cycles where the pre-synaptic spike was high.
    for step, spike in zip(steps, spikes):
        if spike:
            ax.axvspan(step - 0.5, step + 0.5, color=SPIKE_BAND, lw=0, zorder=0)

    ax.plot(steps, [v / 256 for v in i_raw], color=SERIES_I, lw=2, zorder=2)

    # Direct-label the peak rather than every point.
    peak = max(range(len(i_raw)), key=lambda n: i_raw[n])
    ax.annotate(
        f"peak {i_raw[peak] / 256:.3f}  (0x{i_raw[peak]:02X})",
        xy=(steps[peak], i_raw[peak] / 256),
        xytext=(8, -14),
        textcoords="offset points",
        color=INK_MUTED,
        fontsize=8,
    )

    ax.set_title(title, color=INK, fontsize=11, loc="left")
    ax.set_xlabel("clock cycle", color=INK_MUTED, fontsize=9)
    ax.set_ylabel("I_syn  (Q0.8, raw/256)", color=INK_MUTED, fontsize=9)
    ax.set_ylim(0, 1.08)  # headroom so a saturated trace doesn't hug the frame
    ax.grid(axis="y", color=INK_MUTED, alpha=0.15, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK_MUTED)
        ax.spines[side].set_alpha(0.4)
    ax.tick_params(colors=INK_MUTED, labelsize=8)

    # Two visual elements on the plot, so identity is never colour-alone.
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D

    ax.legend(
        handles=[
            Line2D([], [], color=SERIES_I, lw=2, label="I_syn (synaptic current)"),
            Patch(facecolor=SPIKE_BAND, label="pre-synaptic spike high"),
        ],
        frameon=False,
        fontsize=8,
        labelcolor=INK_MUTED,
        loc="upper right",
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


async def run_trace(dut, spike_at, voltage_clamp = int(True)):
    """Reset the DUT, then clock NUM_STEPS cycles driving spikes per spike_at(step).

    Returns (steps, i_raw, spikes, out_spikes). uo_out carries the synaptic
    current I_syn = g * (V - E_rev) in Q0.8, so the raw 8-bit value is
    I_syn / 256. uio_out[0] is the post-synaptic spike output.
    """
    # Set the clock period to 1 ms (1 kHz)
    clock = Clock(dut.clk, 1 / OVERALL_FREQUENCY, unit="sec")
    cocotb.start_soon(clock.start())

    # Reset
    dut._log.info("Reset")
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, RESET_CYCLES)
    dut.rst_n.value = 1

    steps, i_raw, spikes, out_spikes = [], [], [], []
    for step in range(1, NUM_STEPS - RESET_CYCLES + 1):
        spike = 1 if spike_at(step) else 0
        dut.ui_in.value = spike << SPIKE_BIT | voltage_clamp << VOLTAGE_CLAMP_BIT

        await ClockCycles(dut.clk, 1)
        await ReadOnly()  # let the NBA on `r` settle before sampling

        i_syn = int(dut.uo_out.value)
        out_spike = int(dut.uio_out.value) & 1
        dut._log.info(
            f"step {step:3d}  spike={spike}  I_syn={i_syn:3d}  ({i_syn / 256:.3f})"
            f"  out_spike={out_spike}"
        )
        steps.append(step)
        i_raw.append(i_syn)
        spikes.append(spike)
        out_spikes.append(out_spike)

        await NextTimeStep()  # leave ReadOnly so the next iteration can drive ui_in

    # uio_out[0] is the spike output; the rest are tied low and driven as inputs.
    assert dut.uio_oe.value == 0x01
    assert int(dut.uio_out.value) >> 1 == 0

    return steps, i_raw, spikes, out_spikes


def plot_raster(steps, in_spikes, out_spikes, path, title):
    """Save a two-row raster: pre-synaptic input vs. post-synaptic output."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless: no display in CI or the devcontainer
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, ax = plt.subplots(figsize=(8, 2.4), dpi=140)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    rows = [
        ("pre-synaptic in", in_spikes, INK_MUTED, 1),
        ("post-synaptic out", out_spikes, SERIES_I, 0),
    ]
    for label, train, color, y in rows:
        ticks = [s for s, v in zip(steps, train) if v]
        ax.eventplot(
            ticks, lineoffsets=y, linelengths=0.7, linewidths=1.6, colors=color
        )

    ax.set_yticks([r[3] for r in rows], [r[0] for r in rows])
    ax.set_ylim(-0.6, 1.6)
    ax.set_xlim(min(steps) - 0.5, max(steps) + 0.5)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    ax.set_xlabel("clock cycle", color=INK_MUTED, fontsize=9)
    ax.grid(axis="x", color=INK_MUTED, alpha=0.12, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(INK_MUTED)
    ax.spines["bottom"].set_alpha(0.4)
    ax.tick_params(colors=INK_MUTED, labelsize=8, left=False)

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def save_plot(dut, steps, i_raw, spikes, name, title):
    """Write the trace plot, logging whether it landed."""
    png = plot_i(steps, i_raw, spikes, OUTPUT_DIR / name, title)
    if png:
        dut._log.info(f"wrote {png}")
    else:
        dut._log.warning("matplotlib not available - skipped plot")


@cocotb.test()
async def test_single_spike(dut):
    """One spike, then free decay — the impulse response."""
    dut._log.info("Start: single spike, then watch the synaptic current decay")

    steps, i_raw, spikes, _ = await run_trace(
        dut, lambda step: t_spike <= step < t_spike + t_dur
    )

    save_plot(
        dut,
        steps,
        i_raw,
        spikes,
        "i_syn_trace.png",
        "Synaptic current I_syn = g·(V − E_rev) — single spike then decay",
    )


@cocotb.test()
async def test_spike_every_step(dut):
    """Spike on every timestep — drives r to its saturating steady state."""
    dut._log.info("Start: spike every timestep, watch the synaptic current saturate")

    steps, i_raw, spikes, _ = await run_trace(dut, lambda *_: True)

    save_plot(
        dut,
        steps,
        i_raw,
        spikes,
        "i_syn_trace_every_step.png",
        "Synaptic current I_syn = g·(V − E_rev) — spike every timestep",
    )

    # Under continuous drive the state must climb monotonically and settle,
    # never wrap. Saturation at Q08_MAX is a clamp, not an overflow.
    assert all(b >= a for a, b in zip(i_raw, i_raw[1:])), (
        "I_syn must be non-decreasing while spiking every step"
    )
    assert i_raw[-1] == max(i_raw), "I_syn must settle at its maximum, not fall back"


@cocotb.test()
async def test_no_spikes(dut):
    """No input at all — r starts at 0 from reset and must stay there."""
    dut._log.info("Start: no spikes, synaptic current must stay at rest")

    steps, i_raw, spikes, _ = await run_trace(dut, lambda *_: False)

    save_plot(
        dut,
        steps,
        i_raw,
        spikes,
        "i_syn_trace_no_spikes.png",
        "Synaptic current I_syn = g·(V − E_rev) — no spikes (rest)",
    )

    # Nothing drives r, so decaying zero stays zero. A non-zero sample here
    # means the reset didn't take, or an uninitialised term is leaking in.
    assert set(i_raw) == {0}, f"I_syn must stay 0 with no input, saw {sorted(set(i_raw))}"


@cocotb.test()
async def test_neuron_spikes(dut):
    """Unclamped, under continuous drive, V must cross threshold and fire.

    This checks only that spiking happens at all — not when, and not the
    resulting train. Timing depends on LEAK, V_threshold and the driving-force
    format, all still in flux; asserting a train here would just be churn.
    """
    dut._log.info("Start: unclamped, spike every timestep, expect the neuron to fire")

    steps, i_raw, in_spikes, out_spikes = await run_trace(
        dut, lambda *_: True, voltage_clamp=int(False)
    )

    png = plot_raster(
        steps,
        in_spikes,
        out_spikes,
        OUTPUT_DIR / "spike_raster.png",
        "Spike raster — unclamped, pre-synaptic drive every cycle",
    )
    if png:
        dut._log.info(f"wrote {png}")
    else:
        dut._log.warning("matplotlib not available - skipped raster")

    assert any(out_spikes), (
        f"neuron never spiked in {len(steps)} cycles "
        f"(max I_syn={max(i_raw)}, V never reached V_threshold)"
    )
    dut._log.info(f"neuron fired {sum(out_spikes)} times in {len(steps)} cycles")
