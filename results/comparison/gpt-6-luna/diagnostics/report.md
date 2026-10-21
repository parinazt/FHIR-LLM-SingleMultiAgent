# Structural difference diagnostics

Mean ± sample SD across run totals. Positive differences favor multi in count only.

| Type | Resource delta | Paired-output effect | Unpaired-success effect |
|---|---:|---:|---:|
| Patient | -8.00 ± 1.00 | 0.00 ± 0.00 | -8.00 ± 1.00 |
| Condition | -137.00 ± 9.64 | -103.33 ± 5.03 | -33.67 ± 14.15 |
| Observation | 994.00 ± 183.67 | 1132.33 ± 189.47 | -138.33 ± 14.15 |
| MedicationStatement | -33.67 ± 17.50 | -3.33 ± 13.32 | -30.33 ± 9.71 |
| MedicationRequest | -22.67 ± 7.02 | -11.67 ± 5.03 | -11.00 ± 5.29 |
| ALL | 792.67 ± 203.36 | 1014.00 ± 202.65 | -221.33 ± 30.53 |

The decomposition is arithmetic, not a causal explanation. Inspect review_examples.jsonl against source notes before attributing differences to omissions, grouping or unsupported additions.
