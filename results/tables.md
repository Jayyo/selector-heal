Thresholds chosen on 568 validation rows (budget 2% wrong answers per attempt, at least 30 attempts): retrieval 13.76, gate 0.572, model 2.01

### lookalike deletions, test: 2018 rows from 55 site groups

| method | answered | correct / S | wrong picks | false repairs / D | wrong per attempt | successor recall | deletion detection |
|---|---|---|---|---|---|---|---|
| Top retrieved candidate, always answers | 2018/2018 (1.00) | 1210/1356 | 146 | 662/662 | 0.400 [0.364, 0.439] | 0.892 [0.863, 0.921] | 0.000 [0.000, 0.000] |
| Top candidate if retrieval score >= tau | 406/2018 (0.20) | 380/1356 | 7 | 19/662 | 0.064 [0.036, 0.098] | 0.280 [0.239, 0.324] | 0.971 [0.952, 0.987] |
| Fallback chain over recorded locators | 759/2018 (0.38) | 629/1356 | 22 | 108/662 | 0.171 [0.116, 0.233] | 0.464 [0.389, 0.545] | 0.837 [0.780, 0.890] |
| Learned gate (logistic, 12 features) + top candidate | 1100/2018 (0.55) | 1055/1356 | 4 | 41/662 | 0.041 [0.026, 0.058] | 0.778 [0.721, 0.832] | 0.938 [0.914, 0.959] |
| Qwen2.5-3B-Instruct, not fine-tuned | 1241/2018 (0.61) | 532/1356 | 411 | 298/662 | 0.571 [0.539, 0.600] | 0.392 [0.358, 0.428] | 0.550 [0.503, 0.597] |
| Fine-tuned, own abstain point (margin >= 0) | 1258/2018 (0.62) | 1161/1356 | 53 | 44/662 | 0.077 [0.053, 0.104] | 0.856 [0.818, 0.890] | 0.934 [0.906, 0.959] |
| Fine-tuned, margin >= tau | 1101/2018 (0.55) | 1054/1356 | 24 | 23/662 | 0.043 [0.028, 0.060] | 0.777 [0.731, 0.822] | 0.965 [0.945, 0.982] |
| Fine-tuned abstain decision + top candidate | 1258/2018 (0.62) | 1112/1356 | 102 | 44/662 | 0.116 [0.090, 0.143] | 0.820 [0.782, 0.855] | 0.934 [0.906, 0.959] |

### lookalike deletions, val: 568 rows from 14 site groups

| method | answered | correct / S | wrong picks | false repairs / D | wrong per attempt | successor recall | deletion detection |
|---|---|---|---|---|---|---|---|
| Top retrieved candidate, always answers | 568/568 (1.00) | 313/351 | 38 | 217/217 | 0.449 | 0.892 | 0.000 |
| Top candidate if retrieval score >= tau | 135/568 (0.24) | 133/351 | 0 | 2/217 | 0.015 | 0.379 | 0.991 |
| Fallback chain over recorded locators | 204/568 (0.36) | 162/351 | 19 | 23/217 | 0.206 | 0.462 | 0.894 |
| Learned gate (logistic, 12 features) + top candidate | 290/568 (0.51) | 285/351 | 0 | 5/217 | 0.017 | 0.812 | 0.977 |
| Fine-tuned, own abstain point (margin >= 0) | 344/568 (0.61) | 326/351 | 5 | 13/217 | 0.052 | 0.929 | 0.940 |
| Fine-tuned, margin >= tau | 305/568 (0.54) | 299/351 | 2 | 4/217 | 0.020 | 0.852 | 0.982 |
| Fine-tuned abstain decision + top candidate | 344/568 (0.61) | 302/351 | 29 | 13/217 | 0.122 | 0.860 | 0.940 |

### uniform deletions, test: 2026 rows from 55 site groups

| method | answered | correct / S | wrong picks | false repairs / D | wrong per attempt | successor recall | deletion detection |
|---|---|---|---|---|---|---|---|
| Top retrieved candidate, always answers | 2026/2026 (1.00) | 1201/1346 | 145 | 680/680 | 0.407 [0.369, 0.444] | 0.892 [0.862, 0.922] | 0.000 [0.000, 0.000] |
| Top candidate if retrieval score >= tau | 395/2026 (0.19) | 370/1346 | 11 | 14/680 | 0.063 [0.033, 0.095] | 0.275 [0.232, 0.319] | 0.979 [0.967, 0.990] |
| Fallback chain over recorded locators | 730/2026 (0.36) | 625/1346 | 25 | 80/680 | 0.144 [0.090, 0.202] | 0.464 [0.392, 0.541] | 0.882 [0.831, 0.926] |
| Learned gate (logistic, 12 features) + top candidate | 1084/2026 (0.54) | 1052/1346 | 6 | 26/680 | 0.030 [0.017, 0.043] | 0.782 [0.727, 0.834] | 0.962 [0.945, 0.978] |
| Qwen2.5-3B-Instruct, not fine-tuned | 1234/2026 (0.61) | 532/1346 | 413 | 289/680 | 0.569 [0.533, 0.603] | 0.395 [0.359, 0.433] | 0.575 [0.535, 0.617] |
| Fine-tuned, own abstain point (margin >= 0) | 1264/2026 (0.62) | 1161/1346 | 46 | 57/680 | 0.081 [0.059, 0.105] | 0.863 [0.828, 0.895] | 0.916 [0.891, 0.942] |
| Fine-tuned, margin >= tau | 1107/2026 (0.55) | 1049/1346 | 28 | 30/680 | 0.052 [0.035, 0.072] | 0.779 [0.735, 0.823] | 0.956 [0.938, 0.973] |
| Fine-tuned abstain decision + top candidate | 1264/2026 (0.62) | 1110/1346 | 97 | 57/680 | 0.122 [0.090, 0.154] | 0.825 [0.784, 0.863] | 0.916 [0.891, 0.942] |

### uniform deletions, val: 551 rows from 14 site groups

| method | answered | correct / S | wrong picks | false repairs / D | wrong per attempt | successor recall | deletion detection |
|---|---|---|---|---|---|---|---|
| Top retrieved candidate, always answers | 551/551 (1.00) | 318/356 | 38 | 195/195 | 0.423 | 0.893 | 0.000 |
| Top candidate if retrieval score >= tau | 133/551 (0.24) | 132/356 | 0 | 1/195 | 0.008 | 0.371 | 0.995 |
| Fallback chain over recorded locators | 192/551 (0.35) | 165/356 | 17 | 10/195 | 0.141 | 0.463 | 0.949 |
| Learned gate (logistic, 12 features) + top candidate | 290/551 (0.53) | 287/356 | 1 | 2/195 | 0.010 | 0.806 | 0.990 |
| Fine-tuned, own abstain point (margin >= 0) | 342/551 (0.62) | 326/356 | 5 | 11/195 | 0.047 | 0.916 | 0.944 |
| Fine-tuned, margin >= tau | 306/551 (0.56) | 299/356 | 2 | 5/195 | 0.023 | 0.840 | 0.974 |
| Fine-tuned abstain decision + top candidate | 342/551 (0.62) | 306/356 | 25 | 11/195 | 0.105 | 0.860 | 0.944 |

