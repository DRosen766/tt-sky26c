# Surfer batch commands, applied after tb.fst loads (surfer -c).
# Commands are the same ones the in-app command palette accepts; `#` comments
# and blank lines are ignored. Errors are reported on stderr as
# "Error on batch commands line N" -- surfer still opens, just without that item.
# Arguments are whitespace/punctuation-split, so divider names must be a single
# token: `divider_add Clock_reset` is fine, `divider_add Clock-reset` is not.

scope_select tb

divider_add Clock_reset
variable_add tb.clk
variable_add tb.rst_n
variable_add tb.ena

divider_add Inputs
variable_add tb.ui_in
variable_add tb.uio_in

divider_add Outputs
variable_add tb.uo_out
variable_add tb.uio_out
variable_add tb.uio_oe

# Everything inside the DUT. Redundant while project.v is still a flat adder
# (its only signals are the ports above), but this is where internal state --
# the synapse accumulator, the shifted-in tau/weight -- will show up.
divider_add DUT
scope_add tb.user_project

zoom_fit
