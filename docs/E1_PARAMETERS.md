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

Status of the former open items: bandwidths and the rate mapping are fixed in
`configs/e1/e1_3_link_budget.json` (E1.3); antenna regimes and the O2I model
are fixed below and in `configs/e1/e1_4_antennas_o2i.json` (E1.4). Still open:
calibration of the rate mapping against 5G-LENA (E1.5).

## E1.4 antenna and deployment sources

| Source | Retrieved | Bytes | SHA-256 |
|---|---|---|---|
| ETSI TR 138 901 V16.1.0 (as above) | 2026-11-06 | 1,444,732 | `513daf94...` |
| ITU-R M.2412-0 (as above) | 2026-11-06 | 3,435,073 | `bbbe7f3c...` |
| Nokia, "Coverage evaluation of 7-15 GHz bands from existing sites", white paper, (c) 2024, https://www.nokia.com/asset/i/213702/ | 2026-11-07 | 708,435 | `c240137ffac12c85261bf80d66139bbe1e85267fbb303b5436305ece49d88376` |
| 3GPP TS 38.101-1 Rel-19 clause 7.2, iTecSpec mirror, https://itecspec.com/spec/3gpp-38-101-1-7-2-diversity-characteristics | 2026-11-07 | 1,367,645 (HTML) | `313b9482ef9f7fbd96fdded4a9360966469d58e7a558b570d52d317bbfacf428` |

The TS 38.101-1 text comes from a mirror; it must be confirmed against the
official 3GPP/ETSI publication before submission. The Nokia white paper is an
industry evaluation, not a standard; its own footnote 1 says the
configurations "do not reflect or anticipate product characteristics but serve
to the purpose of evaluating coverage and performance trends". Regime C is
therefore labelled "vendor evaluation configuration" wherever it is used.

### Antennas

| Item | Value | Label and source |
|---|---|---|
| gNB element pattern | 8 dBi; 65 deg H and V 3 dB beamwidth; SLA_V = A_max = 30 dB | SOURCED: TR 38.901 Table 7.3-1; 8 dBi also M.2412 Table 5(b) and Nokia Table 1 |
| Sectors | 3, bearings 30 / 150 / 270 deg; mechanical tilt 0 | SOURCED: TR 38.901 Table 7.8-1 (its 102 deg calibration downtilt is not used: steering is per UE) |
| gNB gain counting | one polarisation: G_E,max + 10 log10(rows x cols) | Reproduces Nokia Table 1 nominal gains: 8x16x2 -> 29.07 dBi ("29 dBi"), 16x32x2 -> 35.09 dBi ("35 dBi") |
| Steering | ideal, every element (TRX port) steered to the geometric UE direction | UPPER BOUND; Nokia Figure 1 measured 10 dBi in NLOS vs 14.5 dBi nominal (sensitivity S3) |
| Vertical subarray (sensitivity S4) | 4 elements per TRX, 10 deg electrical downtilt | SOURCED: Nokia Table 1 ("4:1 aggregation of radiators in elevation, 10 deg electrical downtilt"); weights TR 38.901 clause 7.3.1 |
| 3.5 GHz array | 8 x 16 x 2 = 256 elements | SOURCED: Nokia Table 1 (64 TRX); within M.2412 "up to 256 Tx/Rx" |
| 7.2 GHz array (B, C) | 16 x 32 x 2 = 1024 elements | ADAPTED: Nokia Table 1 7 GHz array; for B it is the equal-aperture array rounded down (exact factor (7.2/3.5)^2 = 4.23, used 4.00) |
| UE receive elements | 4 (3.5 GHz; A/B at both carriers), 8 (7 GHz in C) | SOURCED: TS 38.101-1 clause 7.2, minimum 4 Rx in n77/n78/n79 and n104 (6425-7125 MHz); 4 Rx at 7.2 GHz ADAPTED from n104; 8 Rx from Nokia Table 1 |
| UE transmit | 23 dBm e.i.r.p., no transmit-array gain | SOURCED: power class 3 (M.2412); Nokia Table 1 "23 dBm max average EIRP for UE" |
| C powers and bandwidths | 53 dBm / 100 MHz (3.5 GHz); 53 or 43 dBm / 200 MHz (7 GHz) | SOURCED: Nokia Table 1 and its two 7 GHz EIRP levels (88 and 78 dBm per 200 MHz) |
| C 7.2 GHz PRBs | 546 at 30 kHz SCS | ASSUMPTION: two aggregated 100 MHz carriers; no 200 MHz FR1 channel in TS 38.101-1 Rel-19 |
| C noise figures | 5 dB gNB, 7 dB UE | ASSUMPTION: M.2412 Configuration A; not stated by Nokia |

### Outdoor-to-indoor and in-car loss

| Item | Value | Source |
|---|---|---|
| Building model | PL_b(d3D-out + d3D-in) + PL_tw + 0.5 d2D-in + N(0, sigma_P^2) | TR 38.901 clause 7.4.3.1, eq. (7.4-2) = M.2412 Annex 1 section 3.2, model B (Table A1-8) |
| PL_tw low-loss / high-loss | 12.70 / 13.66 dB and 26.85 / 31.46 dB at 3.5 / 7.2 GHz | Tables 7.4.3-1 and 7.4.3-2 (PL_npi = 5 dB) |
| sigma_P | 4.4 dB low-loss, 6.5 dB high-loss | Table 7.4.3-2 |
| d2D-in | min of two U(0, 25 m) | Table 7.4.3-2 note (UMa) |
| O2I shadow fading | 7 dB | Table 7.5-6 Part-1, UMa O2I column |
| Car penetration | N(9, 5^2) dB, 0.6-60 GHz | clause 7.4.3.2 = M.2412 section 3.3 |
| Deployment mix | 80% indoor (80% low-loss, 20% high-loss buildings), 20% outdoor in-car | M.2412 Table 5(b) Dense Urban-eMBB |

Not used, and why: TR 38.901 Table 7.4.3-3 / M.2412 model A below 6 GHz
(20 dB wall loss, d2D-in ~ U(0, 25 m), sigma_SF 7 dB) is restricted to
single-frequency simulations below 6 GHz. Applied at 3.5 GHz next to the
frequency-dependent model at 7.2 GHz, it would give the lower carrier the
larger wall loss (20 dB vs 13.7 dB low-loss): an artefact of switching models
at 6 GHz, not physics.

Indoor geometry (stated with every result): ground floor only; each outdoor
position is the facade point, whose geometric LOS state is used for the
outdoor part of the O2I link; the UE lies d2D-in further along the gNB-UE
direction. Upper floors (M.2412 floor distribution) need building-resolved
indoor positions and are deferred to E1c; the ground floor is conservative for
NLOS links.
