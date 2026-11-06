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

| Quantity | 3.5 GHz layer | 7.2 GHz layer |
|---|---|---|
| BS PSD, noise figures, element gains | SOURCED: Configuration A (4 GHz) values | ASSUMPTION: Configuration A values; sensitivity: Configuration B noise figures (BS 7 dB, UE 10 dB) |
| UE maximum power | SOURCED: 23 dBm (M.2412; 3GPP power class 3) | ASSUMPTION: 23 dBm (no 7.125-8.4 GHz UE power class is specified yet) |
| Carrier bandwidth | to be fixed per E1 regime and cited | to be fixed per E1 regime; the 7.125-8.4 GHz IMT range is under study for WRC-27 agenda item 1.7, with ITU-R WP 5D characteristics still in working documents |

Open items before E1 link budgets run: bandwidth per carrier, array
configurations per antenna regime (A: equal elements, B: equal aperture,
C: deployment-realistic), O2I model (TR 38.901 low/high loss), and the
mapping from SINR to rate (to be calibrated against E1b).
