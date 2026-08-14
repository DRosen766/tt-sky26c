<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

A discrete-time first-order kinetic (Markovian) synapse driving a leaky
integrate-and-fire membrane.

The receptor state `r` — the fraction of open post-synaptic receptors — follows
`dr/dt = alpha*(1-r) - beta*r`, integrated with the exact (closed-form) solution
rather than forward Euler, so it is stable at any timestep. One 16x16 multiply
covers both branches; only the steady-state offset differs:

- pre-synaptic spike high: `r <- r*e^-(alpha+beta) + r_inf*(1 - e^-(alpha+beta))`
- pre-synaptic spike low:  `r <- r*e^-beta`

with `alpha = 25/256`, `beta = 56/256`, `r_inf = alpha/(alpha+beta) = 25/81`.
The three coefficients are stored as their exponentials in Q0.16, not as rates.

Conductance is `g = g_max * r`, and the synaptic current is
`I_syn = g * (V - E_rev)`. That current, less a linear leak term, integrates the
membrane potential `V`. When `V` crosses `V_threshold` the neuron fires: the
spike output asserts for one cycle and `V` resets to zero.

`r` and everything derived from it are unsigned Q0.16; `V`, `E_rev` and the leak
are unsigned Q0.8. Every rescale is a power-of-two slice with round-to-nearest,
never a divider, and each stage saturates instead of wrapping. `r` carries the
extra width because a slow tau otherwise decays by less than half an LSB of
Q0.8, which would latch the state instead of relaxing it.

Coefficients are compile-time constants for now; they become shift-in registers
once the load path exists.

### Voltage clamp

With `voltage_clamp` high, `V` is held and the driving force is replaced by
unity, so `uo_out` reports the conductance waveform directly. This is a
measurement mode for characterizing the kinetics, not a physiological clamp at a
command potential.

### Known limitation

Unclamped, the `V - E_rev` subtraction is unsigned: when `V < E_rev` it wraps to
a large positive value instead of going negative. The membrane path needs a
signed format before it models an inhibitory synapse correctly.

## How to test

Simulation runs from `test/` with cocotb and Icarus:

```sh
cd test
make -B
```

Note that `make` exits 0 even when assertions fail — check `results.xml` for
`failure` rather than the exit code.

The suite covers the impulse response (one spike, then free decay), continuous
drive to the saturating steady state, the rest condition (no input, current must
stay at zero), and unclamped firing. It writes current traces and a spike raster
to `test/output/`.

To drive the design directly: hold `ui[7]` high for the cycles a pre-synaptic
spike is present, set `ui[6]` to choose clamped (conductance readout) or
unclamped (full membrane dynamics) mode, and watch `uo_out` for the synaptic
current and `uio_out[0]` for output spikes.

## External hardware

None.
