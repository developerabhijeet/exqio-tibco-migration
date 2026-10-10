# PoC toolkit: prototype extractor and parsed inventory

## extract_ir.py
Deterministic parser for TIBCO BusinessWorks process files into one target-neutral model (IR).
- BW5 .process (namespace xmlns.tibco.com/bw/process/2003): starter, activities, groups, transitions with conditions, input-binding XPath reads, shared resources, SQL, Java Code flag
- BW6 .bwp (WS-BPEL 2.0 + tibex): extension activities with activityTypeID, BPEL reply/invoke/throw/pick, transition conditions, links, SQL, XPath reads from mappings
- Complexity score (Simple / Medium / Complex) from activity count, groups, SQL, Java and conditional transitions

Run (Python 3, standard library only):

    python3 extract_ir.py <folder with the unzipped samples>

Writes ir_inventory.json in the current folder and prints totals.

## ir_inventory.json
Output from the original run over the three cloned repositories (147 process files: 129 BW5, 18 BW6; 451 activities and groups; 66 activity types; zero parse failures). That run covered the whole tibco-codereview repository, so counts differ slightly from the files in these zips.

## Status
Prototype for the demo. Next: XSLT lineage graph, global variable and substvar resolution, EMS config reader, spec-pack writer, Postgres + pgvector indexer.
