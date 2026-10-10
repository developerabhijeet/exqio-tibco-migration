# PoC sample 3: BusinessWorks 5 projects from fastconnect/tibco-codereview

Source: github.com/fastconnect/tibco-codereview.
Licence: Apache License 2.0 (see LICENSE_Apache-2.0.txt). The most freely usable of the three sample sets; keep the licence file with any copy.

## What is inside
- bw5_demo_project: a BW5 project built to exercise code-review rules (maintainability, performance and design issues): deep subprocess levels, many activities, unlabelled and coloured transitions, starter processes with output
- bw5_codereview_project: the BW5 project that implements the code-review tool itself (mapper-heavy, many Call Process activities)

## Useful for the PoC
- Parser robustness and scale: 66 BW5 process files with heavy XSLT mapping
- Risk Reviewer seeding: the demo project's folders name the issue each process contains, so they double as a labelled test set for findings
