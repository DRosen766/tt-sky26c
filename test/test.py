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

CFG_DIN_BIT = 0  # ui_in[0] is the serial config data input
CFG_SHIFT_BIT = 1  # ui_in[1] shifts one bit in and freezes the neuron state

CFG_BITS = 48  # {MAT_EXP_SPK, MAT_EXP_NSPK, B_SPK}, 16 bits each, MSB first

# Must track the *_DEFAULT localparams in project.v. Shifting these in has to
# be indistinguishable from shifting nothing in at all.
MAT_EXP_SPK_DEFAULT = 47760  # Q0.16  e^-(alpha+beta)
MAT_EXP_NSPK_DEFAULT = 52659  # Q0.16  e^-beta
B_SPK_DEFAULT = 5486  # Q0.16  r_inf*(1 - e^-(alpha+beta))

CFG_DEFAULTS = (MAT_EXP_SPK_DEFAULT, MAT_EXP_NSPK_DEFAULT, B_SPK_DEFAULT)

V_THRESHOLD = 192  # Q0.8, must track V_threshold in project.v
t_spike = 10
t_dur = 10  # cycles the pre-synaptic spike is held high


# Chart palette (light surface). One series, so no cycling.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
SERIES_I = "#2a78d6"
SERIES_V = "#c2570f"
SERIES_CFG = "#7a3fb8"
SPIKE_BAND = "#e8e8e4"


def draw_i_axes(ax, steps, i_raw, spikes, title, color=SERIES_I, ylabel=True):
    """Draw one I_syn trace onto an existing axes, styled like the rest of the suite.

    Shared so a single trace and a side-by-side comparison are drawn by the same
    code -- if the two panels of a comparison were styled independently, a
    difference in framing could read as a difference in the data.
    """
    ax.set_facecolor(SURFACE)

    # Shade the cycles where the pre-synaptic spike was high.
    for step, spike in zip(steps, spikes):
        if spike:
            ax.axvspan(step - 0.5, step + 0.5, color=SPIKE_BAND, lw=0, zorder=0)

    ax.plot(steps, [v / 256 for v in i_raw], color=color, lw=2, zorder=2)

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
    if ylabel:
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
    return ax


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

    draw_i_axes(ax, steps, i_raw, spikes, title)

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


async def shift_in_config(dut, mat_exp_spk, mat_exp_nspk, b_spk):
    """Serially load the three kinetic coefficients, MSB first.

    cfg_shift freezes the neuron while it is high, so the load costs the design
    no simulated time: the trace either side of it is what it would have been
    with no load at all. Leaves ui_in at 0.
    """
    word = (mat_exp_spk << 32) | (mat_exp_nspk << 16) | b_spk
    for pos in range(CFG_BITS - 1, -1, -1):
        bit = (word >> pos) & 1
        dut.ui_in.value = bit << CFG_DIN_BIT | 1 << CFG_SHIFT_BIT
        await ClockCycles(dut.clk, 1)
    dut.ui_in.value = 0
    dut._log.info(
        f"shifted in MAT_EXP_SPK={mat_exp_spk} MAT_EXP_NSPK={mat_exp_nspk} "
        f"B_SPK={b_spk} (0x{word:012X})"
    )


async def run_trace(dut, spike_at, voltage_clamp = int(True), cfg = None):
    """Reset the DUT, then clock NUM_STEPS cycles driving spikes per spike_at(step).

    Returns (steps, i_raw, spikes, out_spikes, v_centred). uo_out carries the
    synaptic current I_syn = g * (V - E_rev) in Q0.8, so the raw 8-bit value is
    I_syn / 256. uio_out is {V[7:1], spike}: bit 0 is the post-synaptic spike
    and the upper 7 bits are the membrane potential with its LSB dropped.

    cfg, when given, is (MAT_EXP_SPK, MAT_EXP_NSPK, B_SPK) to shift in after
    reset before the trace starts. Left None, the chain stays at its reset value of 0
    and the design falls back to its built-in defaults.
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

    if cfg is not None:
        await shift_in_config(dut, *cfg)

    steps, i_raw, spikes, out_spikes, v_centred = [], [], [], [], []
    for step in range(1, NUM_STEPS - RESET_CYCLES + 1):
        spike = 1 if spike_at(step) else 0
        dut.ui_in.value = spike << SPIKE_BIT | voltage_clamp << VOLTAGE_CLAMP_BIT

        await ClockCycles(dut.clk, 1)
        await ReadOnly()  # let the NBA on `r` settle before sampling

        i_syn = int(dut.uo_out.value)
        uio = int(dut.uio_out.value)
        out_spike = uio & 1
        v_even, v_mid = v_from_uio(uio)
        dut._log.info(
            f"step {step:3d}  spike={spike}  I_syn={i_syn:3d}  ({i_syn / 256:.3f})"
            f"  V~{v_even:3d}  out_spike={out_spike}"
        )
        steps.append(step)
        i_raw.append(i_syn)
        spikes.append(spike)
        out_spikes.append(out_spike)
        v_centred.append(v_mid)

        await NextTimeStep()  # leave ReadOnly so the next iteration can drive ui_in

    # All inputs arrive on ui_in, so the bidir bus is driven as output throughout.
    assert dut.uio_oe.value == 0xFF, (
        f"uio_oe must drive the whole bus, saw 0x{int(dut.uio_oe.value):02X}"
    )

    return steps, i_raw, spikes, out_spikes, v_centred


def v_from_uio(uio):
    """Recover the membrane potential from uio_out = {V[7:1], spike}.

    V[0] never leaves the chip, so the pin can only resolve V to even Q0.8
    codes: a 1-LSB step, half the resolution V actually has internally. Taking
    the pin value as-is would bias every sample low by that lost bit, so add
    back half a step -- the midpoint of the interval the true V must lie in.
    Error is then +/-0.5 LSB and centred, instead of -1..0 and one-sided.

    Returns (raw_even, centred) in Q0.8 units, the second a float.
    """
    raw_even = uio & 0xFE  # uio_out[7:1] shifted back into place; V[0] lost
    return raw_even, raw_even + 0.5


def plot_i_compare(steps, left, right, spikes, path, title, labels):
    """Save two I_syn traces side by side on a shared y scale.

    Side by side rather than overlaid: the point of the pair is that the two are
    identical, and an overlay of identical traces shows one line, which is
    indistinguishable from having plotted only one. Two panels make the claim
    checkable by eye, and the per-sample assertion carries the proof.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless: no display in CI or the devcontainer
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, (ax_l, ax_r) = plt.subplots(
        1, 2, figsize=(11, 4), dpi=140, sharey=True
    )
    fig.patch.set_facecolor(SURFACE)

    draw_i_axes(ax_l, steps, left, spikes, labels[0], color=SERIES_I)
    draw_i_axes(ax_r, steps, right, spikes, labels[1], color=SERIES_CFG, ylabel=False)

    fig.suptitle(title, color=INK, fontsize=12, x=0.01, ha="left")

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def draw_v_axes(ax, steps, v_centred, out_spikes, title, color=SERIES_V, ylabel=True):
    """Draw one membrane-potential trace onto an existing axes.

    Shared between the single-trace plot and the side-by-side comparison so both
    are framed identically -- a styling difference between two panels meant to
    look the same would read as a difference in the data.
    """
    ax.set_facecolor(SURFACE)

    # Fired cycles as a tick strip, not shaded spans: under heavy drive the
    # neuron fires nearly every cycle and per-cycle spans would fill the axes.
    # V resets on these, so a drop right after a tick is a reset, not decay.
    fired_at = [s for s, f in zip(steps, out_spikes) if f]
    if fired_at:
        ax.eventplot(
            fired_at,
            lineoffsets=0.035,
            linelengths=0.05,
            linewidths=1.2,
            colors=INK_MUTED,
            alpha=0.7,
            zorder=3,  # above the trace: the reset drop lands on the same x
        )

    # Steps, not a smooth line: the pin resolves V only to even codes, and
    # interpolating between them would imply precision the readout doesn't have.
    ax.step(
        steps,
        [v / 256 for v in v_centred],
        where="post",
        color=color,
        lw=1.8,
        zorder=2,
    )
    ax.axhline(V_THRESHOLD / 256, color=INK_MUTED, lw=1, ls="--", alpha=0.6, zorder=1)

    ax.set_title(title, color=INK, fontsize=11, loc="left")
    if ylabel:
        ax.set_ylabel("V  (Q0.8, raw/256)", color=INK_MUTED, fontsize=9)
    ax.set_ylim(0, 1.08)
    ax.set_xlim(min(steps) - 0.5, max(steps) + 0.5)
    ax.grid(axis="y", color=INK_MUTED, alpha=0.15, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(INK_MUTED)
    ax.spines["left"].set_alpha(0.4)
    ax.tick_params(colors=INK_MUTED, labelsize=8, bottom=False)

    from matplotlib.lines import Line2D

    ax.legend(
        handles=[
            Line2D([], [], color=color, lw=1.8, label="V (7-bit readout, +½ LSB)"),
            Line2D([], [], color=INK_MUTED, lw=1, ls="--", label="V_threshold"),
            Line2D([], [], color=INK_MUTED, lw=1.2, alpha=0.55, label="fired (V reset)"),
        ],
        frameon=False,
        fontsize=8,
        labelcolor=INK_MUTED,
        loc="upper right",
    )
    return ax


def draw_presyn_raster(ax_r, steps, in_spikes, ylabel=True):
    """Draw the pre-synaptic input raster on an axes sharing x with a trace above."""
    ax_r.set_facecolor(SURFACE)
    ax_r.eventplot(
        [s for s, v in zip(steps, in_spikes) if v],
        lineoffsets=0,
        linelengths=0.8,
        linewidths=1.4,
        colors=INK_MUTED,
    )
    if ylabel:
        ax_r.set_ylabel(
            "pre-syn", color=INK_MUTED, fontsize=8, rotation=0, ha="right", va="center"
        )
    ax_r.set_ylim(-0.6, 0.6)
    ax_r.set_yticks([])
    ax_r.set_xlabel("clock cycle", color=INK_MUTED, fontsize=9)
    ax_r.grid(axis="x", color=INK_MUTED, alpha=0.12, lw=0.8)
    ax_r.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax_r.spines[side].set_visible(False)
    ax_r.spines["bottom"].set_color(INK_MUTED)
    ax_r.spines["bottom"].set_alpha(0.4)
    ax_r.tick_params(colors=INK_MUTED, labelsize=8, left=False)
    return ax_r


def plot_v(steps, v_centred, out_spikes, in_spikes, path, title):
    """Save the membrane potential over time, reconstructed from 7 of its 8 bits.

    A pre-synaptic raster shares the x axis underneath, so the input driving
    each charge phase lines up with the response above it.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless: no display in CI or the devcontainer
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, (ax, ax_r) = plt.subplots(
        2,
        1,
        figsize=(8, 4.6),
        dpi=140,
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.12},
    )
    fig.patch.set_facecolor(SURFACE)

    draw_v_axes(ax, steps, v_centred, out_spikes, title)
    draw_presyn_raster(ax_r, steps, in_spikes)

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def plot_v_compare(steps, left, right, out_left, out_right, in_spikes, path, title, labels):
    """Save two membrane-potential traces side by side, each over its input raster.

    V is the sensitive observable for a coefficient change -- it integrates the
    error rather than rounding it away at the Q0.8 current pin -- so this is the
    panel where a broken load path would show first.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless: no display in CI or the devcontainer
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, axes = plt.subplots(
        2,
        2,
        figsize=(11, 4.6),
        dpi=140,
        sharey="row",
        sharex="col",
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.12},
    )
    fig.patch.set_facecolor(SURFACE)

    for col, (v, out, label, color) in enumerate(
        ((left, out_left, labels[0], SERIES_V), (right, out_right, labels[1], SERIES_CFG))
    ):
        draw_v_axes(axes[0][col], steps, v, out, label, color=color, ylabel=(col == 0))
        draw_presyn_raster(axes[1][col], steps, in_spikes, ylabel=(col == 0))

    fig.suptitle(title, color=INK, fontsize=12, x=0.01, ha="left")

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


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

    steps, i_raw, spikes, *_ = await run_trace(
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

    steps, i_raw, spikes, *_ = await run_trace(dut, lambda *_: True)

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

    steps, i_raw, spikes, *_ = await run_trace(dut, lambda *_: False)

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
    resulting train. Timing depends on LEAK, V_threshold and E_rev, all still
    in flux; asserting a train here would just be churn.
    """
    dut._log.info("Start: unclamped, spike every timestep, expect the neuron to fire")

    steps, i_raw, in_spikes, out_spikes, v_centred = await run_trace(
        dut, lambda *_: True, voltage_clamp=int(False)
    )

    for png, what in (
        (
            plot_raster(
                steps,
                in_spikes,
                out_spikes,
                OUTPUT_DIR / "spike_raster.png",
                "Spike raster — unclamped, pre-synaptic drive every cycle",
            ),
            "raster",
        ),
        (
            plot_v(
                steps,
                v_centred,
                out_spikes,
                in_spikes,
                OUTPUT_DIR / "v_trace_every_step.png",
                "Membrane potential V — unclamped, drive every cycle "
                "(7-bit readout, ±½ LSB)",
            ),
            "V trace",
        ),
    ):
        if png:
            dut._log.info(f"wrote {png}")
        else:
            dut._log.warning(f"matplotlib not available - skipped {what}")

    assert any(out_spikes), (
        f"neuron never spiked in {len(steps)} cycles "
        f"(max I_syn={max(i_raw)}, V never reached V_threshold)"
    )
    dut._log.info(f"neuron fired {sum(out_spikes)} times in {len(steps)} cycles")


@cocotb.test()
async def test_v_readout_burst(dut):
    """Membrane potential over time under a burst, read back off uio_out[7:1].

    Driving every cycle makes V degenerate — it crosses threshold, resets, and
    is sampled at 0 almost every cycle. A finite burst lets V charge, fire, and
    then decay, which is what the readout is actually for.
    """
    dut._log.info("Start: unclamped burst, trace the membrane potential")

    steps, _, in_spikes, out_spikes, v_centred = await run_trace(
        dut,
        lambda step: t_spike <= step < t_spike + t_dur,
        voltage_clamp=int(False),
    )

    png = plot_v(
        steps,
        v_centred,
        out_spikes,
        in_spikes,
        OUTPUT_DIR / "v_trace.png",
        f"Membrane potential V — {t_dur}-cycle burst at t={t_spike} "
        "(7-bit readout, ±½ LSB)",
    )
    if png:
        dut._log.info(f"wrote {png}")
    else:
        dut._log.warning("matplotlib not available - skipped V trace")

    # The readout drops V[0], so every sample must land on an even code.
    # An odd one means the pin mapping slipped and the spike bit is bleeding in.
    assert all((v - 0.5) % 2 == 0 for v in v_centred), (
        f"V readout must be even codes + 1/2 LSB, saw {sorted(set(v_centred))[:8]}"
    )


@cocotb.test()
async def test_shift_in_defaults_match(dut):
    """Shifting in the default coefficients must reproduce the unconfigured trace.

    Two runs of the same burst stimulus: one with the config chain left at its
    reset value of 0 (so the design falls back to its built-in MAT_EXP_*), one
    with those same constants shifted in explicitly. The shift-in path is only
    correct if it is invisible here -- any difference means a bit-order slip in
    the chain, a wrong sentinel, or the load leaking simulated time into the
    neuron state.
    """
    dut._log.info("Start: default coefficients vs. the same values shifted in")

    burst = lambda step: t_spike <= step < t_spike + t_dur

    steps, i_default, spikes, out_default, v_default = await run_trace(
        dut, burst, voltage_clamp=int(False)
    )
    steps_cfg, i_cfg, spikes_cfg, out_cfg, v_cfg = await run_trace(
        dut,
        burst,
        voltage_clamp=int(False),
        cfg=CFG_DEFAULTS,
    )

    panels = (
        "config chain at reset (built-in defaults)",
        f"shifted in: SPK={MAT_EXP_SPK_DEFAULT}, "
        f"NSPK={MAT_EXP_NSPK_DEFAULT}, B={B_SPK_DEFAULT}",
    )
    for png, what in (
        (
            plot_i_compare(
                steps,
                i_default,
                i_cfg,
                spikes,
                OUTPUT_DIR / "i_syn_shift_in_compare.png",
                "Shift-in equivalence — synaptic current",
                panels,
            ),
            "I_syn comparison",
        ),
        (
            # V is where a coefficient error accumulates instead of being
            # rounded off at the 8-bit current pin, so plot it alongside.
            plot_v_compare(
                steps,
                v_default,
                v_cfg,
                out_default,
                out_cfg,
                spikes,
                OUTPUT_DIR / "v_shift_in_compare.png",
                "Shift-in equivalence — membrane potential (7-bit readout, ±½ LSB)",
                panels,
            ),
            "V comparison",
        ),
    ):
        if png:
            dut._log.info(f"wrote {png}")
        else:
            dut._log.warning(f"matplotlib not available - skipped {what}")

    assert steps == steps_cfg and spikes == spikes_cfg, (
        "both runs must see identical stimulus"
    )

    # Compare every observable, not just I_syn: a coefficient error that the
    # current pin happens to round away would still move V and the spike train.
    for name, a, b in (
        ("I_syn", i_default, i_cfg),
        ("V", v_default, v_cfg),
        ("out_spike", out_default, out_cfg),
    ):
        mismatch = [(s, x, y) for s, x, y in zip(steps, a, b) if x != y]
        assert not mismatch, (
            f"{name} differs after shifting in the defaults: "
            f"{len(mismatch)} of {len(steps)} samples, first at step "
            f"{mismatch[0][0]} ({mismatch[0][1]} vs {mismatch[0][2]})"
        )

    dut._log.info(f"all {len(steps)} samples identical across both runs")


@cocotb.test()
async def test_shift_in_changes_behaviour(dut):
    """Every field of the chain must actually reach the datapath.

    The equivalence test above passes trivially if the config registers are
    never read, so pair it with one load per field that has to change the
    trace. Each is checked against the default run on the axis that field
    controls, which also pins the bit order: a swapped field would move the
    wrong part of the waveform.
    """
    dut._log.info("Start: perturb each coefficient in turn, expect a changed trace")

    burst = lambda step: t_spike <= step < t_spike + t_dur
    tail = t_spike + t_dur + 5  # well into free decay, after the drive stops

    steps, i_default, *_ = await run_trace(dut, burst)

    # MAT_EXP_NSPK: halved, so the idle decay is far faster. Only the free-decay
    # tail is governed by it, and it can never hold more charge than the default.
    _, i_fast, *_ = await run_trace(
        dut, burst, cfg=(MAT_EXP_SPK_DEFAULT, 32768, B_SPK_DEFAULT)
    )
    assert all(f <= d for f, d in zip(i_fast, i_default)), (
        "a faster idle decay can never hold more charge than the default"
    )
    assert i_fast[tail] < i_default[tail], (
        f"MAT_EXP_NSPK had no effect at step {steps[tail]}: "
        f"{i_fast[tail]} vs default {i_default[tail]}"
    )

    # B_SPK: doubled, so each driven cycle injects twice as much. The peak sits
    # inside the burst, which is the part B_SPK drives.
    _, i_big_b, *_ = await run_trace(
        dut, burst, cfg=(MAT_EXP_SPK_DEFAULT, MAT_EXP_NSPK_DEFAULT, 2 * B_SPK_DEFAULT)
    )
    assert max(i_big_b) > max(i_default), (
        f"B_SPK had no effect: peak {max(i_big_b)} vs default {max(i_default)}"
    )

    # MAT_EXP_SPK: halved, so r is pulled down harder on every driven cycle and
    # the burst cannot climb as high.
    _, i_small_spk, *_ = await run_trace(
        dut, burst, cfg=(32768, MAT_EXP_NSPK_DEFAULT, B_SPK_DEFAULT)
    )
    assert max(i_small_spk) < max(i_default), (
        f"MAT_EXP_SPK had no effect: peak {max(i_small_spk)} "
        f"vs default {max(i_default)}"
    )

    dut._log.info(
        f"peaks - default={max(i_default)} big_B={max(i_big_b)} "
        f"small_SPK={max(i_small_spk)}; tail at step {steps[tail]}: "
        f"fast_NSPK={i_fast[tail]} default={i_default[tail]}"
    )
