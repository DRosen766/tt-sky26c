# Testbench

cocotb + Icarus. `tb.v` instantiates `tt_um_sky26c` and exposes the pin buses.

## Run

```sh
make -B                  # RTL sim -> tb.fst, results.xml
make -B FST=             # VCD instead (also edit tb.v's $dumpfile)
make clean
```

Select tests:

```sh
COCOTB_TESTCASE=test_single_spike make -B
COCOTB_TEST_MODULES=test,test_kinetics make -B
```

`make` exits 0 even when assertions fail — check `results.xml`, not `$?`.

## Gate level

Harden first, then copy `../runs/wokwi/results/final/verilog/gl/tt_um_sky26c.v`
to `gate_level_netlist.v`:

```sh
make -B GATES=yes
```

## Waveforms

```sh
gtkwave tb.fst tb.gtkw
surfer tb.fst            # or: make surf
```

## Notes

- cocotb 2.x API: `Clock(dut.clk, 10, unit="us")`, `COCOTB_TEST_MODULES` /
  `COCOTB_TESTCASE` (not `MODULE` / `TESTCASE`)
- deps pinned in `requirements.txt`
- plots are written to `output/` (gitignored)
