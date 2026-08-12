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
  // All output pins must be assigned. If not used, assign to 0.
  assign uio_out = 8'h00;    // Bidir pins unused
  assign uio_oe  = 8'h00;    // ...so hold them as inputs

  // ---------------------------------------------------------------------------
  // Fixed-point format.
  //   The receptor state r and everything derived from it are unsigned Q0.16:
  //   raw value v represents v / 65536, range [0, 0.99998].
  //   The membrane side (V, E_rev, leak) stays unsigned Q0.8 (v / 256).
  //   Scales are powers of two so rescaling is a slice, never a divider.
  //
  //   Why Q0.16 for r: the per-step decay is r*(1 - e^-beta). A slow tau makes
  //   that change smaller than half an LSB, and round-to-nearest then returns r
  //   unchanged -- every state below the floor becomes a fixed point and the
  //   synapse latches instead of decaying. In Q0.8 that floor sat at r = 0.5,
  //   which a tau_decay of 256 steps ran straight into. Q0.16 moves it to 0.002.
  //   The current rates decay ~35%/step so they clear it easily; the width is
  //   kept so slower taus stay available without another format change.
  // ---------------------------------------------------------------------------
  localparam [ 7:0] Q08_MAX  = 8'hFF;    // largest Q0.8,  ~0.99609
  localparam [15:0] Q016_MAX = 16'hFFFF; // largest Q0.16, ~0.99998

  // Coefficients. Become shift-in registers once the load path exists.
  // Rate constants, Q0.8 per timestep: alpha = 16/256 = 0.0625, beta = 112/256 = 0.4375.
  // Chosen so alpha + beta = 0.5 exactly, which sets the pulse-on time constant:
  //   tau       = 1/(alpha+beta) = 2.00 steps   (relaxation while the pulse is on)
  //   tau_decay = 1/beta         = 2.29 steps   (after the pulse ends)
  //   r_inf     = alpha/(alpha+beta) = 0.125    (where it settles under the pulse)
  // The sum of the rates sets how fast equilibrium is reached; their ratio sets
  // where it lands. tau < tau_decay always holds, for any positive alpha.
  localparam [7:0] ALPHA = 8'd16;   // Q0.8  forward (binding) rate per timestep
  localparam [7:0] BETA  = 8'd112;  // Q0.8  backward (unbinding) rate per timestep

  // Matrix exponentials for the exact (closed-form) update, Q0.16. Both are
  // e^(-rate) rounded to 16 bits, NOT the rates themselves:
  //   MAT_EXP_SPK  = e^-((ALPHA+BETA)/256) * 65536 = e^-0.5000 * 65536 = 39749.6
  //   MAT_EXP_NSPK = e^-(BETA/256)         * 65536 = e^-0.4375 * 65536 = 42313.2
  // WARNING: these are hand-entered and do NOT track ALPHA/BETA. Changing a rate
  // without recomputing its exponential silently changes the modelled tau. Storing
  // a rate here instead of its exponential is the easy mistake -- it looks
  // plausible and still simulates.
  localparam [15:0] MAT_EXP_SPK  = 16'd39750;  // Q0.16  e^-(alpha+beta), pulse on
  localparam [15:0] MAT_EXP_NSPK = 16'd42313;  // Q0.16  e^-beta,         pulse off

  // B_SPK = (1 - e^-(alpha+beta)) * r_inf, Q0.16, where r_inf = ALPHA/(ALPHA+BETA).
  // Check: the fixed point of the spiking branch is
  //   B_SPK / (65536 - MAT_EXP_SPK) = 3223 / 25786 = 0.1250 = r_inf, exactly.
  localparam [15:0] B_SPK = 16'd3223;  // Q0.16  (1 - e^-(alpha+beta)) * r_inf = 0.0492

  localparam [7:0] g_max = 8'd255;  // Q0.8  maximum conductance, ~0.99609
  localparam [7:0] reverse_potential = 8'd32;  // Q0.8  reversal potential, 32/256 = 0.125

  // inputs
  wire pre_synaptic_spike = ui_in[7];  // spike input
  wire voltage_clamp = ui_in[6];  // 1 = hold V, so I_syn reports g directly


  // State
  reg [15:0] r;  // Q0.16  fraction of open receptors
  reg [ 7:0] V;  // Q0.8   membrane potential


  // Exact update of dr/dt = alpha*(1-r) - beta*r, solved in closed form:
  //   pulse on :  r <- r*e^-(alpha+beta) + r_inf*(1 - e^-(alpha+beta))
  //   pulse off:  r <- r*e^-beta                      (relaxes toward 0, no offset)
  // Both branches are a multiply by an exponential; only the offset differs, so
  // selecting the coefficient BEFORE the multiply keeps this to one 16x16
  // multiplier instead of one per branch.
  wire [15:0] mat_exp = pre_synaptic_spike ? MAT_EXP_SPK : MAT_EXP_NSPK;

  // Q0.16 * Q0.16 -> Q0.32; +32768 then slicing [31:16] is the rounded >> 16.
  wire [31:0] r_mul_q32 = r * mat_exp;
  wire [31:0] r_mul_rnd = r_mul_q32 + 32'd32768;
  wire [15:0] r_decay   = r_mul_rnd[31:16];  // Q0.16

  // 17 bits of headroom on the offset, then saturate rather than wrap.
  wire [16:0] r_sum  = {1'b0, r_decay} + (pre_synaptic_spike ? {1'b0, B_SPK} : 17'd0);
  wire [15:0] r_next = r_sum[16] ? Q016_MAX : r_sum[15:0];

  // Conductance g = g_max * r.  Q0.16 * Q0.8 -> Q0.24; slice [23:8] back to Q0.16.
  wire [23:0] g_q24 = r_next * g_max;
  wire [23:0] g_rnd = g_q24 + 24'd128;
  wire [15:0] g     = g_rnd[23:8];  // Q0.16 conductance

  // Driving force (V - E_rev), Q0.8 in 9 bits.
  // Under voltage clamp it is exactly 1.0 = 9'd256, so I_syn = g * 1 = g and the
  // pin reports the conductance waveform directly. Note 1.0 is 256 in Q0.8, NOT
  // 1 -- a driving force of 9'd1 is 1/256 and quantises g away to nothing.
  // ⚠ Unclamped, this subtraction is UNSIGNED: V < E_rev wraps to a large
  // positive instead of going negative. Needs a signed format before the
  // unclamped path means anything.
  wire [8:0] driving_force = voltage_clamp ? 9'd256
                                           : ({1'b0, V} - {1'b0, reverse_potential});

  // Synaptic current I_syn = g * (V - E_rev).  Q0.16 * Q0.8 -> Q0.24;
  // +128 then slicing [24:8] is the rounded >> 8, leaving Q0.16.
  wire [24:0] I_syn_q24  = g * driving_force;
  wire [24:0] I_syn_rnd  = I_syn_q24 + 25'd128;
  wire [16:0] I_syn_full = I_syn_rnd[24:8];
  wire [15:0] I_syn      = I_syn_full[16] ? Q016_MAX : I_syn_full[15:0];  // Q0.16

  // The pin is 8 bits: the top byte of Q0.16 is the same number in Q0.8.
  wire [7:0] I_syn_q8 = I_syn[15:8];

  // Leak current ~ 0.1 * V. A Q0.8 multiply, not a divider: cheaper, and it
  // keeps the same round-and-slice idiom as everything else. (V / 10 followed
  // by [15:8] scaled the result down a second time and was always 0.)
  localparam [7:0] LEAK = 8'd26;  // Q0.8  leak conductance, 26/256 = 0.102
  wire [15:0] I_leak_q16 = V * LEAK + 16'd128;
  wire [ 7:0] I_leak     = I_leak_q16[15:8];

  // Update neuron potential: V + I_syn - I_leak, with headroom then clamped
  // at both ends so it saturates instead of wrapping.
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

    end else begin
      // update population of open receptors
      r <= r_next;

      // update membrane potential if voltage not clamped.
      // Under clamp V is held: driving_force already substitutes unity, so V
      // plays no part in I_syn and must not drift.
      if (!voltage_clamp) begin
        V <= V_next;
      end
    end
`ifndef SYNTHESIS
    $strobe("t=%0t clamp=%0d spike=%0d r=%0d g=%0d uo_out=%h", $time, voltage_clamp, pre_synaptic_spike, r, g, uo_out);
`endif
  end
  assign uo_out = I_syn_q8;  // synaptic current, top byte of Q0.16

  // List all unused inputs to prevent warnings
  // The low bits of the rounded products are the deliberate Q0.16 -> Q0.8 discard.
  // ALPHA/BETA are documentation for where the exponentials came from; the
  // datapath uses the exponentials, not the rates.
  wire _unused = &{ena, uio_in, ui_in[5:0], r_mul_rnd[15:0], g_rnd[7:0],
                   I_syn_rnd[7:0], I_syn[7:0], I_leak_q16[7:0],
                   ALPHA, BETA, 1'b0};

endmodule
