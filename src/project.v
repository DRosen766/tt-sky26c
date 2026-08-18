/*
 * Copyright (c) 2026 Danny Rosen
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_sky26c (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // enable
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);
  assign uio_oe = 8'hFF;  // all bidir pins are outputs

  localparam [ 7:0] Q08_MAX  = 8'hFF;
  localparam [15:0] Q016_MAX = 16'hFFFF;

  // Q0.16 defaults for dr/dt = alpha*(1-r) - beta*r, with alpha = 25/256,
  // beta = 56/256, r_inf = alpha/(alpha+beta) = 25/81.
  localparam [15:0] MAT_EXP_SPK_DEFAULT  = 16'd47760;  // e^-(alpha+beta)
  localparam [15:0] MAT_EXP_NSPK_DEFAULT = 16'd52659;  // e^-beta
  localparam [15:0] B_SPK_DEFAULT        = 16'd5486;   // r_inf*(1 - e^-(alpha+beta))

  localparam [7:0] reverse_potential = 8'd255;  // Q0.8  E_rev, excitatory
  localparam [7:0] V_threshold       = 8'd192;  // Q0.8
  localparam [7:0] LEAK              = 8'd16;   // Q0.8

  wire pre_synaptic_spike = ui_in[7];
  wire voltage_clamp      = ui_in[6];  // 1 = hold V, I_syn reports g
  wire cfg_din            = ui_in[0];
  wire cfg_shift          = ui_in[1];  // 1 = shift cfg, neuron frozen

  reg [15:0] r;  // Q0.16  fraction of open receptors
  reg [ 7:0] V;  // Q0.8   membrane potential
  reg [ 0:0] spike;

  reg [47:0] cfg;  // MSB first: MAT_EXP_SPK, MAT_EXP_NSPK, B_SPK
  wire [15:0] cfg_mat_exp_spk  = cfg[47:32];
  wire [15:0] cfg_mat_exp_nspk = cfg[31:16];
  wire [15:0] cfg_b_spk        = cfg[15:0];

  // All-zero field = never loaded, so use the default.
  wire [15:0] mat_exp_spk  = |cfg_mat_exp_spk  ? cfg_mat_exp_spk  : MAT_EXP_SPK_DEFAULT;
  wire [15:0] mat_exp_nspk = |cfg_mat_exp_nspk ? cfg_mat_exp_nspk : MAT_EXP_NSPK_DEFAULT;
  wire [15:0] b_spk        = |cfg_b_spk        ? cfg_b_spk        : B_SPK_DEFAULT;

  //   spike:  r <- r*e^-(alpha+beta) + B_SPK
  //   idle :  r <- r*e^-beta
  wire [15:0] mat_exp = pre_synaptic_spike ? mat_exp_spk : mat_exp_nspk;

  wire [31:0] r_mul_q32 = r * mat_exp;  // Q0.32
  wire [31:0] r_mul_rnd = r_mul_q32 + 32'd32768;
  wire [15:0] r_decay   = r_mul_rnd[31:16];

  wire [16:0] r_sum  = {1'b0, r_decay} + (pre_synaptic_spike ? {1'b0, b_spk} : 17'd0);
  wire [15:0] r_next = r_sum[16] ? Q016_MAX : r_sum[15:0];

  // g = r*255/256. x*255 == (x<<8) - x; written as `x * 255` Yosys builds a
  // full multiplier array instead of strength-reducing it.
  wire [23:0] g_q24 = {r_next, 8'h00} - {8'h00, r_next};
  wire [23:0] g_rnd = g_q24 + 24'd128;
  wire [15:0] g     = g_rnd[23:8];  // Q0.16

  // Driving force (E_rev - V), signed Q0.8; unity under clamp.
  wire signed [9:0] driving_force =
      voltage_clamp ? 10'sd256
                    : $signed({2'b00, reverse_potential}) - $signed({2'b00, V});

  // Q0.16 * Q0.8 -> Q0.24, rounded and sliced to Q0.8.
  wire signed [26:0] I_syn_q24 = $signed({1'b0, g}) * driving_force;
  wire signed [26:0] I_syn_rnd = I_syn_q24 + 27'sd32768;
  wire signed [10:0] I_syn_q8  = I_syn_rnd[26:16];

  // Unsigned pin: negative current reads as 0.
  wire [7:0] I_syn_pin = I_syn_q8[10]                  ? 8'h00
                       : (I_syn_q8 > $signed(11'd255)) ? Q08_MAX
                       : I_syn_q8[7:0];

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
      r     <= 16'h0000;
      V     <= 8'h00;
      spike <= 1'b0;
      cfg   <= 48'h0000_0000_0000;
    end else if (cfg_shift) begin
      cfg <= {cfg[46:0], cfg_din};
    end else begin
      r <= r_next;

      // V is held under clamp: it does not feed I_syn and must not drift.
      if (!voltage_clamp) begin
        if (spike) begin
          // Reset the cycle after firing: keeps the crossing visible on the
          // readout, and gives a one-cycle refractory.
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

  assign uo_out  = I_syn_pin;  // Q0.8
  assign uio_out = {V[7:1], spike};

  // Unused inputs and the deliberate Q0.16 -> Q0.8 discards.
  wire _unused = &{ena, uio_in, ui_in[5:2], r_mul_rnd[15:0], g_rnd[7:0],
                   I_syn_rnd[15:0], I_leak_q16[7:0], Q016_MAX,
                   1'b0};

endmodule
