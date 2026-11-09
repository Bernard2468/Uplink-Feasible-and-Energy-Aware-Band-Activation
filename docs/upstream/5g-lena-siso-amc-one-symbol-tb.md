# Draft issue for 5G-LENA: SISO AMC sizes its TBLER test for one OFDM symbol

Status: DRAFT. Not filed; filing is the repository owner's decision. Check the tracker for
duplicates first.

## Versions

- ns-3: ns-3.48 (our tree `d083a17a`).
- 5G-LENA: `5g-lena-v5.1.y`, commit `cedceadd`.

## Summary

`NrAmc::CreateCqiFeedbackSiso` (`model/nr-amc.cc`, about line 229) accepts an MCS when a
transport block of size `CalculateTbSize(mcs, 1, rbMap.size())` has TBLER <= 0.1. The third
argument is the number of resource elements in RB x symbols, so this is a TB that occupies
**one OFDM symbol** of the band.

This function produces:
- the UE's wideband DL CQI (`NrUePhy::CreateDlCqiFeedbackMessage`, `nr-ue-phy.cc:1429`);
- the gNB's UL MCS (`NrMacSchedulerCQIManagement::UlSBCQIReported`,
  `nr-mac-scheduler-cqi-management.cc:79`).

The transport blocks that are then scheduled span 10 to 13 symbols in a 14-symbol slot. They
are 10 to 13 times larger and are segmented into more and longer LDPC code blocks, so their
TBLER under the EESM error models can be far above 0.1.

The MIMO path, `NrAmc::GetMcs` / `CalcTbSizeForMimoMatrix`, sizes the test TB for
`NR_AMC_NUM_SYMBOLS_DEFAULT = 12` symbols. This matches TS 38.214 clause 5.2.2.5.1 ("The number
of PDSCH and DM-RS symbols is equal to 12") with the 0.1 TBLER target of clause 5.2.2.1.

## What we measured

Setup (single cell, single UE, AWGN):
- prescribed path loss, no fading, isotropic 1x1 antennas;
- 20 MHz, numerology 1, 51 RBs, DDDSU, `NrMacSchedulerTdmaPF`;
- full-buffer UDP in both directions;
- `SetDlErrorModel` / `SetUlErrorModel` set to `NrEesmIrT2` or `NrEesmIrT1`;
- 75 path-loss values, 1 dB apart.

Findings:

- The first-transmission BLER exceeded 0.1 at 12 operating points above 0 dB. The worst was
  `NrEesmIrT2`, UL, SINR 26.36 dB: MCS 26, BLER 0.875 (147 of 168).
- The spectral efficiency fell below the value at lower SINR at 9 points.
- **Replay.** We passed every received TB's SINR through the scheduler's own `NrAmc` objects
  (`CreateCqiFeedbackSiso`, then `GetMcsFromCqi` for DL) and through the error model, with
  `CalculateTbSize(mcs, 1, nRb x nSym)`. Over all first transmissions in a 0.4 s window of
  150 runs:
  - the replay reproduced every TB size;
  - it reproduced every chosen MCS;
  - it reproduced every TBLER in the `RxPacketTrace` (absolute difference 0).
- At all 12 points:
  - the one-symbol TB of the chosen MCS has TBLER <= 0.1 (for example 0.0127 at 26.36 dB);
  - the transmitted 12- and 13-symbol TBs have TBLER 0.11 to 0.85 (0.823 and 0.883 at
    26.36 dB).
- Fixing the MCS to the 12-symbol rule (`GetMcs`) removes all 9 drops in spectral efficiency.
  With `NrEesmIrT2` the resulting curve is monotone in both directions. With `NrEesmIrT1` one
  new drop of 0.5% remains (DL, 12.36 dB).

## Expected

The SISO AMC should evaluate the TBLER on the same reference resource as the MIMO path and
TS 38.214: 12 PDSCH+DM-RS symbols. Ideally it would use the TB size that will actually be
scheduled.

## Possible fix (for the maintainers to judge)

In `CreateCqiFeedbackSiso`, size the test TB as
`CalculateTbSize(mcs, rank, rbMap.size() * NR_AMC_NUM_SYMBOLS_DEFAULT)`, as
`CalcTbSizeForMimoMatrix` does.

A related observation, not necessarily a bug: at a fixed SINR and MCS, the EESM TBLER is not
monotone in TB size. For example, `NrEesmIrT2` at 18.36 dB, MCS 17, 51 RBs gives:

| Symbols | TBLER |
|---|---|
| 10 | 0.061 |
| 12 | 0.401 |
| 13 | 0.0036 |

So no single reference size bounds the BLER of every allocation shape.

## Reproduction

Our program and scripts:
- program: `ns3/scratch/cmwave/e15-link-validation.cc`, mode `link`, options `--amcReplay` and
  `--fixedMcsDl/Ul`;
- scripts: `scripts/e1_5_3_link_mapping.py` and `scripts/e1_5_3a_amc_diagnostic.py`;
- protocol: `docs/E1_5_VALIDATION_PROTOCOL.md`, sections E1.5.3 and E1.5.3a.

We can reduce this to a minimal standalone example on request.
