# Non-Lagrangian rainfall-field parameters

This sibling stage of [event LK extraction](event_lk_usage.md) describes every
current rainfall field in the frozen event population, independently of whether
its preceding observation could be tracked. Supporting predecessor fields
outside an event are not included in the parameter population.

## Computation and missingness

The runner reuses the core field-statistics, lower-truncated Normal fit and
isotropic power-spectrum functions. It reads `BOM` observations with
`missing_as_nan=True` and does not use the legacy parameter CLI's zero-filling
path. No rainfall, LK payload or operational parameter record is overwritten.

- Rain occurrence uses the product threshold, 1 mm/h.
- Rain fraction uses the entire domain as denominator, consistent with the
  existing convention. A separate `rain_fraction_valid` reports the fraction
  over observed pixels; `valid_fraction` records that support.
- Physical rainfall mean, population standard deviation, maximum, wet-area
  mean and wet-area quantiles (0.5, 0.9 and 0.99) use observed pixels and mm/h.
- `ordinary_mean_db` and `ordinary_stdev_db` describe retained wet dBR values.
- `latent_mean_db` and `latent_stdev_db` fit the underlying Normal distribution
  using lower-truncated maximum likelihood. The configured cutoff is 1.25 dBR;
  it is distinct from the rain-occurrence threshold of 0 dBR (1 mm/h).
  Undefined fits have null parameters and an explicit fit status/sample count.
- The spectral break is fixed at the configured 15 km. Core `beta_1` describes
  frequencies below the break, `beta_2` those above; these are signed
  power-versus-frequency slopes. Radial means represent Fourier power of the
  standardized dBR field, not annulus-integrated energy. The stored model's
  `scale_break` is in pixels; `scale_break_km` is also recorded explicitly.
- Spectral fitting requires whole-domain WAR at least 0.01, adequate variability
  and a completely observed grid. Partial grids retain valid marginal estimates
  but receive `unavailable_partial_support` for their spectrum. Missing pixels
  are never converted to observed dry rainfall or silently infilled for an FFT.

Observed dry fields are valid inputs with undefined wet/latent statistics as
appropriate. Unavailable fields and processing failures remain separate outcomes.
No advection, cascade autocorrelations, temporal parameters or inferred tracking
truth are estimated in this stage. The fitted Normal family and fixed break
are configured models, not evidence that those assumptions fit every field.

## Storage and reproducibility

`BOM_advection.field_parameters` stores searchable per-field results, source
references and small spectral bin/model arrays. `field_parameter_runs` stores
the manifest and completion summary. Unique identity is run ID, source storm
ID and current valid time. The manifest records the source LK inventory and
generation, selected current-field snapshot hash, estimator configuration,
dependency versions and relevant source-code hashes.

This reuse of the LK inventory establishes the same event membership; field
estimation does not require a successful motion result. In particular, the
22 current fields whose predecessors were unavailable remain included.

Reports live outside Git under
`~/data/alan/psteps_advect_data/runs/field_parameters/<run-id>/`. They contain
the manifest, selected input snapshot and summary. A single-writer lock prevents
concurrent writes to one run. Resume revalidates sources and reuses completed
field results; changed inputs or estimator configuration require a new run ID.
Failures can be retried, while completed runs are immutable.

```bash
conda activate bom_cases
bom-event-field-parameters --dry-run
bom-event-field-parameters --run-id syd_2025_fields_v1
```

The equivalent wrapper is `python scripts/generate_field_parameters.py`.
Use `--lk-run` to select another completed event inventory and `--run-id` for
a separate parameter result set. Defaults select the accepted full Sydney run.

## Verification

The field-parameter pilot, `pilot_fields_v1`, processed all 1,244 current fields
from `pilot_1000_v1` in 11.1 seconds. Every input produced a defined latent fit
and spectrum, including the current field with an unavailable predecessor.
Tests cover physical and dBR marginal units, separate observed/domain
denominators, retained missingness, withheld partial-grid spectra, dry and
constant fields, invalid inputs, null serialization and selection independent
of tracking outcome. The repository suite passed 41 tests.

See the [full population acceptance record](2025_lk_and_field_parameters.md)
for the production run's verified counts and outcomes.
