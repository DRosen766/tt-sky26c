![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# Kinetic synapse + LIF neuron — TTSKY26c

A discrete-time first-order kinetic (Markovian) synapse driving a leaky
integrate-and-fire membrane, hardened for SKY130 via Tiny Tapeout. 1x2 tiles.

- [Datasheet](docs/info.md) — model, pinout, load port, how to test
- [Testbench](test/README.md) — cocotb + Icarus

## Layout

- `src/project.v` — the design; top module `tt_um_sky26c`
- `src/config.json` — LibreLane config
- `info.yaml` — metadata, pinout, tiles, source files
- `test/` — cocotb testbench and plots

Adding a source file means editing both `info.yaml:source_files` and
`PROJECT_SOURCES` in `test/Makefile`.

## Run

```sh
cd test && make -B
```

`make` exits 0 even when assertions fail — grep `results.xml` for `failure`.

## Tiny Tapeout

- [FAQ](https://tinytapeout.com/faq/) · [Digital design lessons](https://tinytapeout.com/digital_design/) · [Discord](https://tinytapeout.com/discord)
- [Harden locally](https://www.tinytapeout.com/guides/local-hardening/)
- [Submit to a shuttle](https://app.tinytapeout.com/)
