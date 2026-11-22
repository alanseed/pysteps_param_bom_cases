# Storm-based characterisation and synthetic tracking trials

Broad strategy agreed on 22 November 2026. This records the scientific
sequence; it does not specify final sampling thresholds or implement a runner.
The [reuse and gap inventory](advection_reuse_inventory.md) identifies the
existing components to adapt.

The [2025 ingestion review and storm-generation record](2025_ingestion_and_storms.md)
documents completion of ingestion and the frozen 107-storm population.
The [event-based LK extraction plan](event_lk_extraction_plan.md) specifies the
proposed first processing stage, storage, restart and pilot checks.

## Scope and population

Keep the current LK input parameter set fixed. LK parameter optimisation and
another parameter sweep are outside the scope of this study.
The adopted Bureau research configuration uses a maximum of 1,000 detected
features per pair (increased from the initial 400-feature pilot); all other
resolved LK settings remain unchanged. Successful and accepted vector counts
can be lower. This is a fixed design choice, not a parameter sweep.

After the full 2025 Sydney archive has been ingested and its coverage checked,
generate the `storms` collection using the named definition in
[the Bureau configuration](../configs/db/config_bom_data.json):

- Name: `syd_1km_qpe_standard_v1`.
- Minimum wetted area ratio: 0.025.
- Minimum storm duration: 6 hours.
- Maximum gap: 30 minutes, subject to the storm builder's missing-field policy.

Freeze the published storm generation ID, definition, input coverage and
software revision. Refer to that generation in downstream results rather than
silently adopting a subsequently regenerated population. Missing observations
must remain distinct from dry observations and must not be bridged to create
LK input pairs.

This population defines the scope of the historical characterisation. Shorter
or otherwise non-qualifying rainfall events are outside it; it is not an
unconditional climatology of every rainy field in the year.

## Event processing

Traverse every event in the frozen population to generate:

1. Sparse LK vectors with the fixed configuration, source-pair identity,
   timestamps, coordinate and velocity conventions, masks and processing
   provenance. Retain tracked vectors and rejection flags, not just accepted
   vectors or an interpolated grid.
2. Explicit pair outcomes, including unavailable inputs, valid inputs with no
   vectors, and successful tracking. Preserve detected, tracked, rejected and
   accepted counts where available.
3. Non-Lagrangian rainfall-field parameters, including rain coverage, wet-area
   intensity and variability, and spatial structure. These provide descriptors
   of the rainfall environment independently of tracking success.
4. Per-pair summaries and event-level descriptors that retain within-event
   distributions as well as central values.

Read preceding observations as needed to process the first pair in an event;
these supporting inputs do not automatically become members of the event's
statistical population. Record nominal and actual source times and resolve
the elapsed-time convention explicitly. Parameter estimation must preserve
missingness; the existing core parameter runner needs its default rainfall
read behaviour reviewed before reuse for this work.

Keep operational rainfall and storm identities in `BOM`. Detailed research
outputs belong in a separate research database, provisionally `BOM_advection`,
with references to source fields and the frozen storm generation. Dense motion
grids are optional outputs for specified analyses and diagnostic cases.

## Representative event selection

Characterise the complete qualifying storm population before selecting a
subset for detailed tracking trials and synthetic-event design. Candidate
descriptors are:

| Environment | Candidate event descriptors |
| --- | --- |
| Rain coverage | Median WAR and its within-event range |
| Intensity and variability | Wet-area dBR mean and standard deviation |
| Spatial organisation | Spectral slopes or characteristic spatial scale |
| Motion | Typical speed and translation-removed vector variability |
| Tracking support | Feature density, accepted fraction and spatial coverage |

Use terciles as an initial approach to covering low, middle and high values.
Inspect descriptor distributions, dependence and event counts before choosing
the final axes. Avoid a full Cartesian product across every descriptor:
five three-level axes already produce 243 combinations, many potentially
empty. Prefer a small number of informative axes and representative events
from occupied groups, supplemented by explicitly identified extremes and
difficult tracking cases.

Calculate sampling boundaries across storms, so long events do not determine
the terciles merely by contributing more frames. Keep representative cases
and deliberately difficult cases labelled separately. Balanced case selection
does not estimate the observed frequency of environments: retain the full
population for those summaries. Define frame/event weighting and uncertainty
explicitly; correlated frames and vectors are not independent replicates.

The final descriptor definitions, number of axes, tercile boundaries, sample
size, case-selection rule and treatment of tied values remain to be specified
after inspecting the observed population. The broad strategy does not commit
to a particular multidimensional stratification.

## Synthetic trials and known truth

Use the observed rainfall and motion structure to define synthetic environments
covering the selected conditions. The
[existing synthetic configuration](../configs/experiments/config_synthetic_event.json)
contains inherited starting values, not a Sydney calibration.

Reuse the existing generation and known-truth evaluation framework. Keep
prescribed latent transport, measured sparse motion and reconstructed dense
motion distinct. Historical observations characterise apparent motion;
consistency diagnostics on those observations do not establish tracking error.

Freeze candidate algorithms and trial definitions before evaluation. Use
independent synthetic evaluation seeds and paired algorithm comparisons with
common support and physical error units. Keep any future classifier fitting
separate from independent evaluation. Record synthetic rainfall evolution and
boundary effects when interpreting differences between apparent motion and
prescribed transport.

Scientific protocols, synthetic trial specifications and paper outputs belong
in the paper repository. Bureau ingestion and event-processing adapters belong
here; shared tracking algorithms belong in the core library.
