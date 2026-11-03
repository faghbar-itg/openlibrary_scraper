# Matching logic

Text comparison applies Unicode NFKC normalization, case folding, whitespace collapse, and punctuation-insensitive matching. RapidFuzz's ratio and token-set ratio are compared and the larger value is used, scaled from 0 to 1.

| Signal | Weight | Evidence |
|---|---:|---|
| Title | 35% | Input title vs. Work title |
| Author | 30% | Input author vs. any Search API author name |
| Publisher | 20% | Target publisher vs. Search API publishers |
| Publication year | 10% | Exact target-year membership in Search API years |
| Language | 5% | Exact normalized language-code membership |

Subject similarity is reported separately and is not part of the weighted score. Missing evidence receives a score of zero. The weighted score is the sum of the five weighted signals.

Defaults classify scores at least 0.85 as `HIGH_CONFIDENCE`, scores at least 0.60 as `POSSIBLE_MATCH`, and lower scores as `LOW_CONFIDENCE`. A below-threshold best candidate is not selected (`NO_MATCH`). When the top two valid Work candidates are within `AMBIGUITY_DELTA` (default 0.03), the best-ranked Work is retained as a possible match and its reason explicitly states the ambiguity.

Search API publisher, year, and language values can aggregate multiple Editions. They are candidate evidence only and do not establish that one Edition matches the input metadata. Invalid/missing Work keys are never turned into invented IDs.
