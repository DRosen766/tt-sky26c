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
  // all three together.
  localparam [15:0] MAT_EXP_SPK  = 16'd47760;  // Q0.16  e^-(alpha+beta)
  localparam [15:0] MAT_EXP_NSPK = 16'd52659;  // Q0.16  e^-beta
  localparam [15:0] B_SPK        = 16'd5486;   // Q0.16  r_inf*(1 - e^-(alpha+beta))

  localparam [7:0] g_max = 8'd255;
  localparam [7:0] reverse_potential = 8'd32;
  localparam [7:0] V_threshold = 8'd128;  // Q0.8  threshold for spike output
  wire pre_synaptic_spike = ui_in[7];
  wire voltage_clamp = ui_in[6];  // 1 = hold V, so I_syn reports g directly

  reg [15:0] r;  // Q0.16  fraction of open receptors
  reg [ 7:0] V;  // Q0.8   membrane potential
  reg [0:0] spike;
  //   spike:  r <- r*e^-(alpha+beta) + B_SPK
  //   idle :  r <- r*e^-beta
  // Selecting the coefficient before the multiply keeps this to one 16x16.
  wire [15:0] mat_exp = pre_synaptic_spike ? MAT_EXP_SPK : MAT_EXP_NSPK;

  wire [31:0] r_mul_q32 = r * mat_exp;  // Q0.32
  wire [31:0] r_mul_rnd = r_mul_q32 + 32'd32768;
  wire [15:0] r_decay   = r_mul_rnd[31:16];

  wire [16:0] r_sum  = {1'b0, r_decay} + (pre_synaptic_spike ? {1'b0, B_SPK} : 17'd0);
  wire [15:0] r_next = r_sum[16] ? Q016_MAX : r_sum[15:0];

  wire [23:0] g_q24 = r_next * g_max;
  wire [23:0] g_rnd = g_q24 + 24'd128;
  wire [15:0] g     = g_rnd[23:8];  // Q0.16 conductance

  // V - E_rev, or unity (256 in Q0.8) under clamp so I_syn = g.
  // Unclamped this subtraction is UNSIGNED: V < E_rev wraps positive.
  wire [8:0] driving_force = voltage_clamp ? 9'd256
                                           : ({1'b0, V} - {1'b0, reverse_potential});

  wire [24:0] I_syn_q24  = g * driving_force;
  wire [24:0] I_syn_rnd  = I_syn_q24 + 25'd128;
  wire [16:0] I_syn_full = I_syn_rnd[24:8];
  wire [15:0] I_syn      = I_syn_full[16] ? Q016_MAX : I_syn_full[15:0];  // Q0.16

  wire [7:0] I_syn_q8 = I_syn[15:8];  // top byte of Q0.16 is the same number in Q0.8

  localparam [7:0] LEAK = 8'd26;  // Q0.8  leak conductance
  wire [15:0] I_leak_q16 = V * LEAK + 16'd128;
  wire [ 7:0] I_leak     = I_leak_q16[15:8];

  // V + I_syn - I_leak, saturating at both ends.
  wire signed [10:0] v_sum = $signed({3'b000, V})
                           + $signed({3'b000, I_syn_q8})
                           - $signed({3'b000, I_leak});
  wire [7:0] V_next = v_sum[10]                    ? 8'h00
                    : (v_sum > $signed(11'd255))   ? Q08_MAX
                    : v_sum[7:0];





  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      r <= 16'h0000;
      V <= 8'h00;
      spike <= 1'b0;
    end else begin
      r <= r_next;

      // Under clamp V is held: it plays no part in I_syn and must not drift.
      if (!voltage_clamp) begin
        if (V_next > V_threshold) begin
          V     <= 8'h00;  // fire and reset
          spike <= 1'b1;
        end else begin
          V     <= V_next;
          spike <= 1'b0;
        end
      end else begin
        spike <= 1'b0;
      end
    end
`ifndef SYNTHESIS
    $strobe("t=%0t clamp=%0d spike=%0d r=%0d g=%0d uo_out=%h", $time, voltage_clamp, pre_synaptic_spike, r, g, uo_out);
`endif
  end
  assign uo_out = I_syn_q8;  // synaptic current, Q0.8
  assign uio_out = {V[7:1],spike};

  // Unused inputs and the deliberate Q0.16 -> Q0.8 discards.
  wire _unused = &{ena, uio_in, ui_in[5:0], r_mul_rnd[15:0], g_rnd[7:0],
                   I_syn_rnd[7:0], I_syn[7:0], I_leak_q16[7:0],
                   1'b0};

endmodule
