# E1 link-budget parameters: sources and assumptions

Status: draft (2026-11-06). Every value is either **SOURCED** (document, table
and page given) or an **ASSUMPTION** (labelled, with the sensitivity range to
report). No value enters an E1 result without one of these labels.

## Primary source

Report ITU-R M.2412-0 (10/2017), "Guidelines for evaluation of radio interface
technologies for IMT-2020", Table 5(b), Dense Urban-eMBB, report pages 22-23
(PDF pages 24-25). Downloaded 2026-11-06 from
https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-M.2412-2017-PDF-E.pdf
(3,435,073 bytes, SHA-256
`bbbe7f3cf352e168cb00df6b2365bc55583837c8e2bda2b148554eba92e07ff3`).

Note: in M.2412 the eMBB environments are Indoor Hotspot, Dense Urban and
Rural. "Urban Macro" exists only for mMTC and URLLC, so the eMBB macro values
below come from the Dense Urban-eMBB macro layer.

## Dense Urban-eMBB macro layer (M.2412 Table 5(b))

| Parameter | Configuration A (4 GHz) | Configuration B (30 GHz) |
|---|---|---|
| BS antenna height | 25 m | 25 m |
| Total transmit power per TRxP | 44 dBm per 20 MHz (41 dBm per 10 MHz) | 40 dBm per 80 MHz (37 dBm per 40 MHz); e.i.r.p. <= 73 dBm |
| UE power class | 23 dBm | 23 dBm; e.i.r.p. <= 43 dBm |
| BS noise figure | 5 dB | 7 dB |
| UE noise figure | 7 dB | 10 dB (high-performance UE; 13 dB may be considered) |
| BS antenna element gain | 8 dBi | 8 dBi |
| UE antenna element gain | 0 dBi | 5 dBi |
| BS antenna elements per TRxP | up to 256 Tx/Rx | up to 256 Tx/Rx |
| UE antenna elements | up to 8 Tx/Rx | up to 32 Tx/Rx |
| Thermal noise density | -174 dBm/Hz | -174 dBm/Hz |
| Inter-site distance | 200 m | 200 m |
| Building types | 20% high-loss, 80% low-loss | same |
| Device deployment | 80% indoor, 20% outdoor (in-car) | same |
| Simulation bandwidth | 20 MHz TDD | 80 MHz TDD |

Derived (no new assumption): 44 dBm per 20 MHz is a power spectral density of
44 - 10 log10(20e6) = -29.0 dBm/Hz.

## Mapping to the two carriers of this study

M.2412 Configuration A is specified at 4 GHz, not at 3.5 GHz. Values carried
over to 3.5 GHz are therefore ADAPTED from a sourced reference, not sourced
for that carrier.

| Quantity | 3.5 GHz layer | 7.2 GHz layer |
|---|---|---|
| BS PSD, noise figures, element gains | ADAPTED from M.2412 Configuration A (4 GHz) | ASSUMPTION (candidate FR3 study configuration): Configuration A values; sensitivity: Configuration B noise figures (BS 7 dB, UE 10 dB) |
| UE maximum power | SOURCED: 23 dBm, power class 3 (M.2412 Table 5(b); 3GPP power class 3 for the 3.5 GHz band n78) | ASSUMPTION: 23 dBm (no 7.125-8.4 GHz UE power class is specified yet) |

### Power accounting rule

The M.2412 BS figure (44 dBm per 20 MHz) is the TOTAL transmit power per
TRxP, summed over all antenna elements. In a link budget it is used once, and
the array contributes only its beamforming gain. The total power must never be
multiplied by the number of elements while also adding array gain (that would
count the array twice). Any configuration must also respect the e.i.r.p.
limits of the reference (BS 73 dBm, UE 43 dBm at 30 GHz) where they apply.
| Carrier bandwidth | to be fixed per E1 regime and cited | to be fixed per E1 regime; the 7.125-8.4 GHz IMT range is under study for WRC-27 agenda item 1.7, with ITU-R WP 5D characteristics still in working documents |

## Propagation model sources (geometry-conditioned TR 38.901 UMa)

Source: ETSI TR 138 901 V16.1.0 (2020-11) = 3GPP TR 38.901 version 16.1.0,
downloaded 2026-11-06 from
https://www.etsi.org/deliver/etsi_tr/138900_138999/138901/16.01.00_60/tr_138901v160100p.pdf
(1,444,732 bytes, SHA-256
`513daf94101121acb74633f09d77836077f6215dfe1aed41caa0a72fd19f2e3b`).
Formulas cross-checked against the ns-3.48 implementation
(`src/propagation/model/three-gpp-propagation-loss-model.cc`).

| Item | Value | Source |
|---|---|---|
| UMa LOS / NLOS path loss | PL1/PL2 with breakpoint; NLOS = max(PL_LOS, PL'_NLOS) | Table 7.4.1-1 |
| Shadow fading std (UMa) | LOS 4 dB, NLOS 6 dB | Table 7.4.1-1 |
| Shadow fading correlation distance (UMa) | LOS 37 m, NLOS 50 m, exponential | Table 7.5-6 (ns-3 `GetShadowingCorrelationDistance`) |
| LOS probability (reference only) | UMa formula | Table 7.4.2-1 |
| Multi-frequency rule | same propagation condition and same normal variate x for the large-scale parameters at all frequencies | Clause 7.6.5 |

Clause 7.6.5, verbatim: "Propagation conditions generated in Step 2 are the
same for all the frequencies." and "The parameters generated in Step 4 are the
same for all the frequencies, except for possibly frequency-dependent scaling
[...] the same value of x is used for all frequencies." Shadow fading is a
Step 4 parameter; its UMa standard deviation does not depend on frequency, so
the shadowing in dB is identical at 3.5 and 7.2 GHz for a given link.

Departure from the standard evaluation (stated in every result): the LOS/NLOS
state is taken deterministically from the Munich scene geometry instead of
being drawn from the Table 7.4.2-1 probability, and the gNB is 27 m high
(TR 38.901 UMa nominal: 25 m). This is a geometry-conditioned hybrid, not an
unmodified 3GPP evaluation scenario.

Consequence to report: with shared shadowing, the model's 3.5 to 7.2 GHz
outdoor path-loss difference is 20 log10(7.2/3.5) = 6.27 dB for every NLOS
link and every LOS link inside the breakpoint distance. It is an assumption of
the model, not a result; E1a.1 ray tracing supports it only for LOS and
near-NLOS links.

Open items before E1 link budgets run: bandwidth per carrier, array
configurations per antenna regime (A: equal elements, B: equal aperture,
C: deployment-realistic), O2I model (TR 38.901 low/high loss), and the
mapping from SINR to rate (to be calibrated against E1b).
