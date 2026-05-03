#!/usr/bin/env python3
import json
from pathlib import Path
HARNESS_FILE=Path("autotreegen_evaluation_harness_trees1_20.json")
def load_engine_output(tree_id):
    p=Path(f"engine_output_{tree_id}.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"tree_id":tree_id,"engine_flags":[],"evaluation_results":{}}
def evaluate_tree(tc,out):
    exp=set(tc.get("expected_engine_flags",[])); act=set(out.get("engine_flags",[]))
    hits=sorted(exp&act); misses=sorted(exp-act)
    assertions=tc.get("evaluation_assertions",[]); reported=out.get("evaluation_results",{})
    passed=sum(1 for a in assertions if reported.get(a.get("assertion_id"),False))
    a_score=passed/len(assertions) if assertions else 1.0
    f_score=len(hits)/len(exp) if exp else 1.0
    s_score=1.0 if tc.get("schema_integrity",{}).get("required_keys_present") else 0.0
    score=a_score*.70+f_score*.20+s_score*.10
    return {"tree_id":tc["tree_id"],"score":round(score,4),"passed":score>=.90,"assertion_score":round(a_score,4),"flag_score":round(f_score,4),"schema_score":s_score,"flag_hits":hits,"flag_misses":misses}
def main():
    h=json.loads(HARNESS_FILE.read_text(encoding="utf-8")); res=[]
    for tc in h["test_cases"]: res.append(evaluate_tree(tc,load_engine_output(tc["tree_id"])))
    overall=sum(r["score"] for r in res)/len(res) if res else 0
    report={"harness_id":h["harness_id"],"overall_score":round(overall,4),"passed":overall>=h["execution_model"]["scoring"]["overall_pass_threshold"],"tree_results":res}
    Path("autotreegen_eval_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(report,indent=2,ensure_ascii=False))
if __name__=="__main__": main()
