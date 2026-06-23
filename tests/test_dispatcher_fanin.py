from manufacturing_agent.graph.dispatcher import orchestrator_dispatcher
from manufacturing_agent.contracts.context import ExecutionPlan, TaskSpec

def test_dispatcher_applies_all_pending_reports():
    plan = ExecutionPlan(intent="combined_analysis", tasks=[
        TaskSpec(task_id="prediction_1", task_type="prediction", status="RUNNING"),
        TaskSpec(task_id="sql_1", task_type="sql", status="RUNNING"),
        TaskSpec(task_id="evidence_1", task_type="evidence", status="RUNNING"),
        TaskSpec(task_id="final_1", task_type="final_answer",
                 depends_on=["prediction_1", "sql_1", "evidence_1"], status="PENDING"),
    ])
    state = {
        "execution_plan": plan,
        "active_task_ids": ["prediction_1", "sql_1", "evidence_1"],
        "consumed_replan_report_indices": [],
        "gate_reports": [
            {"gate_name": "prediction_gate", "status": "PASS", "task_id": "prediction_1"},
            {"gate_name": "sql_gate", "status": "PASS", "task_id": "sql_1"},
            {"gate_name": "evidence_gate", "status": "PASS", "task_id": "evidence_1"},
        ],
    }
    out = orchestrator_dispatcher(state)
    new_plan = out["execution_plan"]
    worker_statuses = [t.status for t in new_plan.tasks if t.task_type != "final_answer"]
    assert all(s == "PASS" for s in worker_statuses)
    assert out["orchestrator_decision"].action == "FINALIZE"