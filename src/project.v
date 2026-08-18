/*
 * Copyright (c) 2024 Danny Rosen
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_sky26c (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);
  // Every input comes from ui_in, so the whole bidir bus is driven as output
  // and nothing contends for it.
  assign uio_oe  = 8'hFF;

  // r and everything derived from it are unsigned Q0.16; V, E_rev and leak are
  // unsigned Q0.8. r needs the extra width or a slow tau decays by < 1/2 LSB
  // and the state latches instead of relaxing.
  localparam [ 7:0] Q08_MAX  = 8'hFF;
  localparam [15:0] Q016_MAX = 16'hFFFF;

  // Exact update of dr/dt = alpha*(1-r) - beta*r, from alpha = 25/256,
  // beta = 56/256, r_inf = alpha/(alpha+beta) = 25/81. Hand-computed; recompute
  // all three together. These are the fallbacks used when nothing has been
  // shifted into the matching config register.
  localparam [15:0] MAT_EXP_SPK_DEFAULT  = 16'd47760;  // Q0.16  e^-(alpha+beta)
  localparam [15:0] MAT_EXP_NSPK_DEFAULT = 16'd52659;  // Q0.16  e^-beta
  localparam [15:0] B_SPK_DEFAULT        = 16'd5486;   // Q0.16  r_inf*(1 - e^-(alpha+beta))

  localparam [7:0] reverse_potential = 8'd255;  // Q0.8  excitatory; must sit above V_threshold
  localparam [7:0] V_threshold = 8'd192;  // Q0.8  threshold for spike output
  wire pre_synaptic_spike = ui_in[7];
  wire voltage_clamp = ui_in[6];  // 1 = hold V, so I_syn reports g directly

  // Serial config port. There is no room to stream 48 bits of coefficient per
  // cycle across the pin budget, so they are shifted in one bit at a time and
  // held in flops. cfg_shift doubles as a hold on the neuron state: while the
  // chain is loading no time passes for r/V/spike, so a load is transparent to
  // the trace either side of it.
  wire cfg_din   = ui_in[0];
  wire cfg_shift = ui_in[1];

  reg [15:0] r;  // Q0.16  fraction of open receptors
  reg [ 7:0] V;  // Q0.8   membrane potential
  reg [0:0] spike;

  // MSB-first chain: shift 48 bits to land MAT_EXP_SPK, MAT_EXP_NSPK, B_SPK.
  reg [47:0] cfg;
  wire [15:0] cfg_mat_exp_spk  = cfg[47:32];
  wire [15:0] cfg_mat_exp_nspk = cfg[31:16];
  wire [15:0] cfg_b_spk        = cfg[15:0];

  // All-zero means "never loaded" -- reset leaves the chain at 0, so an
  // unconfigured part runs on the hand-computed defaults. Zero is not a usable
  // value for any of the three: zero MAT_EXP collapses r every cycle, and zero
  // B_SPK makes the spike input inert. Nothing legitimate is lost by spending
  // it as the sentinel.
  wire [15:0] mat_exp_spk  = |cfg_mat_exp_spk  ? cfg_mat_exp_spk  : MAT_EXP_SPK_DEFAULT;
  wire [15:0] mat_exp_nspk = |cfg_mat_exp_nspk ? cfg_mat_exp_nspk : MAT_EXP_NSPK_DEFAULT;
  wire [15:0] b_spk        = |cfg_b_spk        ? cfg_b_spk        : B_SPK_DEFAULT;

  //   spike:  r <- r*e^-(alpha+beta) + B_SPK
  //   idle :  r <- r*e^-beta
  // Selecting the coefficient before the multiply keeps this to one 16x16.
  wire [15:0] mat_exp = pre_synaptic_spike ? mat_exp_spk : mat_exp_nspk;

  wire [31:0] r_mul_q32 = r * mat_exp;  // Q0.32
  wire [31:0] r_mul_rnd = r_mul_q32 + 32'd32768;
  wire [15:0] r_decay   = r_mul_rnd[31:16];

  wire [16:0] r_sum  = {1'b0, r_decay} + (pre_synaptic_spike ? {1'b0, b_spk} : 17'd0);
  wire [15:0] r_next = r_sum[16] ? Q016_MAX : r_sum[15:0];

  // Peak conductance is full scale, so g = r*255/256, i.e. x*255 == (x<<8) - x.
  // Written as the shift-subtract rather than as `x * 255`: Yosys strength-
  // reduces power-of-two constants but not this one, and as a multiply it
  // synthesises a full array -- 463 cells, which is what pushed the 1x2 tile
  // over 100% placement utilization once the coefficients became runtime
  // values. Bit-identical to the multiply, and 255 is not a knob worth a
  // localparam of its own: any other peak would reintroduce the array.
  wire [23:0] g_q24 = {r_next, 8'h00} - {8'h00, r_next};
  wire [23:0] g_rnd = g_q24 + 24'd128;
  wire [15:0] g     = g_rnd[23:8];  // Q0.16 conductance

  // Driving force (E_rev - V), signed Q0.8: positive below the reversal
  // potential, negative above it, so the current always pulls V TOWARD E_rev.
  // Under clamp it is unity (256 in Q0.8) and I_syn reports g directly.
  wire signed [9:0] driving_force =
      voltage_clamp ? 10'sd256
                    : $signed({2'b00, reverse_potential}) - $signed({2'b00, V});

  // Q0.16 * Q0.8 -> Q0.24, rounded and sliced straight to Q0.8.
  wire signed [26:0] I_syn_q24 = $signed({1'b0, g}) * driving_force;
  wire signed [26:0] I_syn_rnd = I_syn_q24 + 27'sd32768;
  wire signed [10:0] I_syn_q8  = I_syn_rnd[26:16];

  // The pin is unsigned: an inhibitory (negative) current reads as 0.
  wire [7:0] I_syn_pin = I_syn_q8[10]                  ? 8'h00
                       : (I_syn_q8 > $signed(11'd255)) ? Q08_MAX
                       : I_syn_q8[7:0];

  localparam [7:0] LEAK = 8'd16;  // Q0.8  leak conductance
  wire [15:0] I_leak_q16 = V * LEAK + 16'd128;
  wire [ 7:0] I_leak     = I_leak_q16[15:8];

  // V + I_syn - I_leak, saturating at both ends.
  wire signed [12:0] v_sum = $signed({5'd0, V})
                           + $signed({{2{I_syn_q8[10]}}, I_syn_q8})
                           - $signed({5'd0, I_leak});
  wire [7:0] V_next = v_sum[12]                    ? 8'h00
                    : (v_sum > $signed(13'd255))   ? Q08_MAX
                    : v_sum[7:0];





  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      r <= 16'h0000;
      V <= 8'h00;
      spike <= 1'b0;
      cfg <= 48'h0000_0000_0000;
    end else if (cfg_shift) begin
      // Loading: the neuron is frozen, so the coefficients can change without
      // a partially-shifted word ever being applied to a live update.
      cfg <= {cfg[46:0], cfg_din};
    end else begin
      r <= r_next;

      // Under clamp V is held: it plays no part in I_syn and must not drift.
      if (!voltage_clamp) begin
        if (spike) begin
          // Reset the cycle AFTER firing, so the threshold crossing itself is
          // observable on the V readout instead of being overwritten by 0.
          // Also gives a one-cycle refractory: spike cannot stay high.
          V     <= 8'h00;
          spike <= 1'b0;
        end else begin
          V     <= V_next;
          spike <= (V_next > V_threshold);
        end
      end else begin
        spike <= 1'b0;
      end
    end
`ifndef SYNTHESIS
    $strobe("t=%0t clamp=%0d spike=%0d r=%0d g=%0d uo_out=%h", $time, voltage_clamp, pre_synaptic_spike, r, g, uo_out);
`endif
  end
  assign uo_out = I_syn_pin;  // synaptic current, Q0.8
  assign uio_out = {V[7:1],spike};

  // Unused inputs and the deliberate Q0.16 -> Q0.8 discards.
  wire _unused = &{ena, uio_in, ui_in[5:2], r_mul_rnd[15:0], g_rnd[7:0],
                   I_syn_rnd[15:0], I_leak_q16[7:0], Q016_MAX,
                   1'b0};

endmodule
