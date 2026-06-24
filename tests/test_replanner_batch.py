from manufacturing_agent.contracts.context import (
    ExecutionPlan, TaskSpec, SupervisorReplannerDecision, TaskPatch,
)
from manufacturing_agent.graph.replanner import apply_replanner_decision

def test_apply_replanner_with_multiple_patches():
    plan = ExecutionPlan(intent="combined_analysis", tasks=[
        TaskSpec(task_id="sql_1", task_type="sql", status="FAIL"),
        TaskSpec(task_id="evidence_1", task_type="evidence", status="FAIL"),
        TaskSpec(task_id="final_1", task_type="final_answer",
                 depends_on=["sql_1", "evidence_1"], status="PENDING"),
    ])
    decision = SupervisorReplannerDecision(
        action="PATCH_AND_RERUN",
        target_task_ids=["sql_1", "evidence_1"],
        task_patches=[
            TaskPatch(task_id="sql_1", params_update={"strict_schema_check": True}),
            TaskPatch(task_id="evidence_1", params_update={"retrieval_profile": "fallback_broad"}),
        ],
        invalidate_task_ids=["final_1"],
    )
    new_plan = apply_replanner_decision(plan, decision, report=None)
    patched = {t.task_id: t for t in new_plan.tasks}
    assert patched["sql_1"].status == "PENDING" and patched["sql_1"].rerun_count == 1
    assert patched["evidence_1"].status == "PENDING" and patched["evidence_1"].rerun_count == 1
    assert patched["final_1"].status == "PENDING"
    assert new_plan.plan_revision == 1