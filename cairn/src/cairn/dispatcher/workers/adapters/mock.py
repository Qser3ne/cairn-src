from __future__ import annotations

import json

from cairn.dispatcher.config import WorkerConfig, resolve_mock_behavior
from cairn.dispatcher.workers.base import DriverResult, SeedSessionDriver

_SCRIPT = """
import json,random,sys,time

try:
    cfg=json.loads(sys.argv[1])
    prompt=json.loads(sys.argv[2])
    phase=prompt["phase"]
    task_mode=prompt.get("task_mode")
    behavior_phase={
        ("collection","reason"):"collection_reason",
        ("validation","reason"):"validation_reason",
        ("collection","explore_execute"):"collection_explore_execute",
        ("validation","explore_execute"):"validation_explore_execute",
        ("collection","explore_conclude"):"collection_explore_conclude",
        ("validation","explore_conclude"):"validation_explore_conclude",
    }.get((task_mode,phase),phase)
    phase_cfg=cfg[behavior_phase]
except Exception as exc:
    print(f"mock setup failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
delay=phase_cfg["delay"]
time.sleep(random.uniform(delay["min"],delay["max"]))

weights=dict(phase_cfg["outcomes"])
if phase=="reason":
    if not prompt.get("open_intents"):
        weights.pop("noop",None)
    if not prompt.get("fact_ids"):
        weights.pop("intent",None)
choices=[(name,weight) for name,weight in weights.items() if weight>0]
if not choices:
    print(f"mock {behavior_phase} has no legal outcomes for prompt context", file=sys.stderr)
    raise SystemExit(2)

def _rule_matches(rule, prompt):
    fact_ids = prompt.get("fact_ids") or []
    open_intents = prompt.get("open_intents") or []
    if "fact_ids_gte" in rule and len(fact_ids) < rule["fact_ids_gte"]:
        return False
    if "fact_ids_lte" in rule and len(fact_ids) > rule["fact_ids_lte"]:
        return False
    if "open_intents_empty" in rule and (len(open_intents) == 0) != rule["open_intents_empty"]:
        return False
    return True

rules = phase_cfg.get("rules") or []
forced = None
for rule in rules:
    if _rule_matches(rule, prompt):
        forced = rule["force"]
        break

if forced is not None:
    outcome = forced
else:
    pick=random.uniform(0,sum(weight for _,weight in choices))
    total=0
    outcome=choices[-1][0]
    for name,weight in choices:
        total+=weight
        if pick<=total:
            outcome=name
            break

if phase=="healthcheck":
    raise SystemExit(0 if outcome=="ok" else 1)
if outcome=="command_fail":
    print(f"mock {behavior_phase} command failed", file=sys.stderr)
    raise SystemExit(1)
if outcome=="invalid_json":
    print("{invalid json")
    raise SystemExit(0)
if phase=="reason":
    fact_ids=prompt.get("fact_ids") or []
    max_i=prompt.get("max_intents",3)
    task_mode=prompt.get("task_mode")
    has_accounts=bool(prompt.get("has_accounts"))
    collection_fact_ids=[fid for fid in fact_ids if fid!="origin"]
    if outcome=="intent":
        initial_collection = task_mode=="collection" and len(fact_ids)==1 and not prompt.get("open_intents")
        if initial_collection:
            intents=[
                {
                    "from":["origin"],
                    "task_mode":"collection",
                    "auth_scope":"anonymous",
                    "description":"Collect anonymous baseline features, APIs, and auth boundaries from the origin.",
                },
            ]
            if has_accounts:
                intents.append({
                    "from":["origin"],
                    "task_mode":"collection",
                    "auth_scope":"authenticated",
                    "description":"Collect authenticated baseline features, APIs, and auth boundaries from the origin.",
                })
        else:
            source_pool=collection_fact_ids if task_mode=="validation" and collection_fact_ids else fact_ids
            count=random.randint(1,max(1,max_i))
            intents=[]
            for idx in range(count):
                source=source_pool[idx % len(source_pool)] if source_pool else None
                fi=[source] if source else []
                if task_mode=="validation":
                    description=f"Validate vulnerability hypothesis from collection fact {source or 'none'}"
                else:
                    description=f"Collect more feature, API, and auth facts from {source or 'none'}"
                intent={
                    "from":fi,
                    "task_mode":task_mode,
                    "description":description,
                }
                if task_mode=="collection":
                    intent["auth_scope"]="anonymous" if idx % 2 == 0 else "authenticated"
                intents.append(intent)
        print(json.dumps({"accepted":True,"data":{"intents":intents}}, ensure_ascii=False))
    elif outcome=="noop":
        print(json.dumps({"accepted":True,"data":{"decision":"noop","intents":[]}}, ensure_ascii=False))
    elif outcome=="stable":
        print(json.dumps({"accepted":True,"data":{"decision":"no_new_high_value","intents":[]}}, ensure_ascii=False))
    elif outcome=="rejected":
        print(json.dumps({"accepted":False,"reason":"mock_rejected"}, ensure_ascii=False))
    else:
        print(json.dumps({"accepted":True,"data":{"complete":{"description":"mock invalid payload"}}}, ensure_ascii=False))
    raise SystemExit(0)

if phase=="judge":
    if outcome in ("ready","not_ready","blocked"):
        checklist={
            "scope_clarity":{"score":18,"evidence":"mock scope clarity evidence"},
            "feature_coverage":{"score":17,"evidence":"mock feature coverage evidence"},
            "feature_api_mapping_quality":{"score":16,"evidence":"mock route and API mapping evidence"},
            "auth_boundary_coverage":{"score":18,"evidence":"mock auth boundary evidence"},
            "candidate_surface_quality":{"score":17,"evidence":"mock candidate surface evidence"},
        }
        print(json.dumps({"accepted":True,"data":{"verdict":outcome,"score":86,"recommended_action":"create_vuln_project","checklist":checklist,"blocking_gaps":[],"non_blocking_gaps":[]}}, ensure_ascii=False))
    elif outcome=="rejected":
        print(json.dumps({"accepted":False,"reason":"mock_rejected"}, ensure_ascii=False))
    else:
        print(json.dumps({"accepted":True,"data":{"verdict":"unknown"}}), ensure_ascii=False)
    raise SystemExit(0)

if phase=="report":
    if outcome=="draft":
        intent_id=prompt.get("intent_id") or "unknown"
        print(json.dumps({"accepted":True,"data":{"report_markdown":f"# Mock SRC Report\\n\\nDrafted report for {intent_id} from validated findings.","report_json":{"status":"draft","source_intent":intent_id}}}, ensure_ascii=False))
    elif outcome=="rejected":
        print(json.dumps({"accepted":False,"reason":"mock_rejected"}, ensure_ascii=False))
    else:
        print(json.dumps({"accepted":True,"data":{}}, ensure_ascii=False))
    raise SystemExit(0)

if phase=="fork_seed":
    if outcome=="seed":
        print(json.dumps({"accepted":True,"data":{"seed_facts":[{"title":"模拟匿名攻击面","auth_scope":"anonymous","candidate_type":"auth_surface","derived_from":["f001"],"description":"基于 collection f001 生成的模拟 validation seed fact。"}]}}, ensure_ascii=False))
    elif outcome=="rejected":
        print(json.dumps({"accepted":False,"reason":"mock_rejected"}, ensure_ascii=False))
    else:
        print(json.dumps({"accepted":True,"data":{}}, ensure_ascii=False))
    raise SystemExit(0)

if outcome=="fact":
    label = prompt.get("intent_id") or phase
    task_mode = prompt.get("task_mode")
    if task_mode=="collection":
        print(json.dumps({"accepted":True,"data":{
            "description":f"Collection fact from {label}: mapped feature surface, API route, and auth boundary.",
            "fact_type":"feature_surface",
            "title":f"Mock feature surface {label}",
            "summary":"Feature/API/auth baseline fact for validation planning.",
            "details":{
                "features":["orders dashboard","order detail view"],
                "apis":["GET /api/orders","GET /api/orders/{id}"],
                "auth":{"scope":"anonymous_or_authenticated","boundary":"order ownership"},
            },
        }} , ensure_ascii=False))
    elif task_mode=="validation":
        finding={
            "title":f"Mock IDOR finding from {label}",
            "vulnerability_type":"idor",
            "severity":"medium",
            "target":"https://target.test",
            "location":"GET /api/orders/{id}",
            "impact":"An authenticated user may read another user's order data.",
            "evidence":"Changing the order id returned a different account's order in the mock validation flow.",
            "reproduction":"Authenticate as the leased user and request GET /api/orders/1002.",
            "remediation":"Enforce object-level authorization on order resources.",
            "status":"candidate",
            "research_value":"medium",
            "next_action":"report",
        }
        print(json.dumps({"accepted":True,"data":{
            "description":f"Validation fact from {label}: confirmed reportable authorization weakness.",
            "findings":[finding],
        }} , ensure_ascii=False))
    else:
        print(json.dumps({"accepted":True,"data":{"description":f"模拟事实：{label} 已产生一条增量观察。"}} , ensure_ascii=False))
elif outcome=="rejected":
    print(json.dumps({"accepted":False,"reason":"mock_rejected"}, ensure_ascii=False))
else:
    print(json.dumps({"accepted":True,"data":{}}, ensure_ascii=False))
""".strip()


class MockDriver(SeedSessionDriver):
    type_name = "mock"

    @staticmethod
    def _argv(worker: WorkerConfig, prompt: str) -> list[str]:
        behavior = resolve_mock_behavior(worker.name, worker.env)
        return ["python3", "-c", _SCRIPT, json.dumps(behavior, ensure_ascii=False), prompt]

    def build_healthcheck(self, worker: WorkerConfig) -> list[str]:
        return self._argv(worker, '{"phase":"healthcheck"}')

    def build_execute(self, worker: WorkerConfig, prompt: str, session: str | None) -> DriverResult:
        return DriverResult(argv=self._argv(worker, prompt), session=session)

    def build_conclude(self, worker: WorkerConfig, prompt: str, session: str) -> list[str]:
        return self._argv(worker, prompt)
