"""Proof-of-concept: deterministic extraction of TIBCO BusinessWorks process files
(BW5 .process and BW6 .bwp) into a target-neutral intermediate representation (IR).

This is step 1 of the proposed harness: parse first, let the LLM reason over facts.
"""
import glob, html, json, os, re, sys, collections
import xml.etree.ElementTree as ET

def local(tag):
    return tag.split('}', 1)[-1]

def text(el, name):
    for c in el:
        if local(c.tag) == name:
            return (c.text or '').strip()
    return None

def xslt_refs(xml_fragment):
    """Pull the XPath expressions a mapping reads from (data lineage)."""
    return sorted(set(re.findall(r'select="([^"]+)"', xml_fragment)))

def parse_bw5(path):
    root = ET.parse(path).getroot()
    ir = {"engine": "BW5", "file": path, "name": text(root, 'name'),
          "starter": None, "activities": [], "transitions": [], "groups": [],
          "processVariables": [], "sharedResources": set(), "sql": [], "javaCode": False}
    def walk(parent, group=None):
        for el in parent:
            t = local(el.tag)
            if t in ('starter', 'activity', 'group'):
                typ = text(el, 'type')
                act = {"name": el.get('name'), "type": typ, "group": group}
                cfg = next((c for c in el if local(c.tag) == 'config'), None)
                if cfg is not None:
                    raw = ET.tostring(cfg, encoding='unicode')
                    for ref in re.findall(r'>(/SharedResources/[^<]+|/[^<]+\.shared\w+)<', raw):
                        ir["sharedResources"].add(ref)
                    for sql in re.findall(r'<statement>(.*?)</statement>', raw, re.S):
                        ir["sql"].append({"activity": act["name"], "statement": html.unescape(sql).strip()})
                    if 'JavaActivity' in (typ or ''):
                        ir["javaCode"] = True
                ib = next((c for c in el if local(c.tag) == 'inputBindings'), None)
                if ib is not None:
                    act["reads"] = xslt_refs(ET.tostring(ib, encoding='unicode'))
                if t == 'starter':
                    ir["starter"] = act
                elif t == 'group':
                    ir["groups"].append(act)
                    walk(el, el.get('name'))
                else:
                    ir["activities"].append(act)
            elif t == 'transition':
                ir["transitions"].append({"from": text(el, 'from'), "to": text(el, 'to'),
                                          "condition": text(el, 'conditionType'),
                                          "xpath": text(el, 'xpath'), "group": group})
            elif t == 'processVariables':
                ir["processVariables"] = [local(c.tag) for c in el]
    walk(root)
    ir["sharedResources"] = sorted(ir["sharedResources"])
    return ir

def parse_bw6(path):
    s = open(path, encoding='utf-8', errors='ignore').read()
    body = s[s.find('</tibex:Types>'):] if '</tibex:Types>' in s else s
    ir = {"engine": "BW6", "file": path,
          "name": (re.search(r'<bpws:process[^>]*\sname="([^"]+)"', s) or [None, None])[1],
          "starter": None, "activities": [], "transitions": [], "groups": [],
          "processVariables": [], "sharedResources": [], "sql": [], "javaCode": False}
    # extension activities carry a name + activityTypeID
    for m in re.finditer(r'<bpws:extensionActivity>(.*?)</bpws:extensionActivity>', body, re.S):
        blk = m.group(1)
        name = re.search(r'name="([^"]+)"', blk)
        typ = re.search(r'activityTypeID="([^"]+)"', blk)
        act = {"name": name.group(1) if name else None, "type": typ.group(1) if typ else None}
        reads = []
        for e in re.findall(r'expression="([^"]+)"', blk):
            reads += xslt_refs(html.unescape(e))
        if reads:
            act["reads"] = sorted(set(reads))
        if typ and 'java' in typ.group(1).lower():
            ir["javaCode"] = True
        ir["activities"].append(act)
    for m in re.finditer(r'<bpws:(reply|invoke|receive|throw|pick|forEach|if)\b[^>]*?name="([^"]+)"', body):
        ir["activities"].append({"name": m.group(2), "type": "bpel." + m.group(1)})
    for sql in re.findall(r'sqlStatement="([^"]+)"', s):
        ir["sql"].append({"statement": html.unescape(sql).strip()})
    for c in re.findall(r'<bpws:transitionCondition[^>]*>(.*?)</bpws:transitionCondition>', body, re.S):
        ir["transitions"].append({"condition": html.unescape(c).replace('<![CDATA[', '').replace(']]>', '').strip()})
    ir["links"] = re.findall(r'<bpws:link name="([^"]+)"', body)
    ir["sharedResources"] = sorted(set(re.findall(r'property:\w+="([^"]+\.(?:httpClientResource|jdbcResource|jmsConnResource|httpConnResource)[^"]*)"', s)))
    return ir

def complexity(ir):
    n = len(ir["activities"]) + len(ir["groups"])
    score = n + 2 * len(ir["groups"]) + 3 * len(ir["sql"]) + (8 if ir["javaCode"] else 0) \
        + sum(1 for t in ir["transitions"] if t.get("condition") not in (None, 'always'))
    return "Simple" if score < 8 else "Medium" if score < 20 else "Complex"

if __name__ == '__main__':
    base = sys.argv[1] if len(sys.argv) > 1 else '.'
    out = []
    for f in sorted(glob.glob(os.path.join(base, '**/*.process'), recursive=True)):
        try: out.append(parse_bw5(f))
        except ET.ParseError: pass
    for f in sorted(glob.glob(os.path.join(base, '**/*.bwp'), recursive=True)):
        if '.application/' in f:  # packaged duplicate of the module copy
            continue
        out.append(parse_bw6(f))
    for ir in out:
        ir["complexity"] = complexity(ir)
    json.dump(out, open('ir_inventory.json', 'w'), indent=2, default=list)
    c = collections.Counter((i["engine"], i["complexity"]) for i in out)
    types = collections.Counter(a["type"] for i in out for a in i["activities"] + i["groups"] if a.get("type"))
    print(len(out), 'processes parsed'); print(dict(c))
    print('distinct activity types:', len(types))
    print('with SQL:', sum(1 for i in out if i["sql"]), ' with Java code:', sum(1 for i in out if i["javaCode"]))
