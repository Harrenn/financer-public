from __future__ import annotations

from typing import Callable

from app import db as db_module
from app.v2 import finance

from .critic import CriticSafetyAgent
from .executor import ExecutorAgent
from .ontology import OntologyResolverAgent
from .planner import PlannerAgent


class MultiAgentOrchestrator:
    def __init__(
        self,
        *,
        large_amount_threshold: float = 1000.0,
        autocreate_threshold: float = 0.85,
        followup_threshold: float = 0.6,
    ):
        self.planner = PlannerAgent()
        self.ontology = OntologyResolverAgent(
            autocreate_threshold=autocreate_threshold,
            followup_threshold=followup_threshold,
        )
        self.critic = CriticSafetyAgent(large_amount_threshold=large_amount_threshold)
        self.executor = ExecutorAgent()

    def run(
        self,
        *,
        user_text: str,
        run_tool: Callable[[str, dict], dict],
    ) -> dict:
        db = db_module.SessionLocal()
        run = finance.create_agent_run(db, user_input=user_text)
        db.commit()
        db.refresh(run)
        step_order = 1

        try:
            plan = self.planner.plan(user_text)
            finance.create_agent_run_step(
                db,
                run_id=run.id,
                step_order=step_order,
                agent_name="planner",
                input_payload={"user_text": user_text},
                output_payload={
                    "assistant_message": plan.assistant_message,
                    "actions": [{"name": a.name, "arguments": a.arguments} for a in plan.actions],
                    "needs_follow_up": plan.needs_follow_up,
                    "follow_up_question": plan.follow_up_question,
                },
                status="ok",
            )
            db.commit()
            step_order += 1

            if plan.needs_follow_up:
                follow_up = plan.follow_up_question or "Please provide more details."
                finance.finalize_agent_run(db, run.id, assistant_output=follow_up, status="needs_follow_up")
                db.commit()
                return {"assistant_message": follow_up, "tool_outputs": [], "run_id": run.id}

            resolved = self.ontology.resolve(db, plan.actions)
            finance.create_agent_run_step(
                db,
                run_id=run.id,
                step_order=step_order,
                agent_name="ontology_resolver",
                input_payload={"actions": [{"name": a.name, "arguments": a.arguments} for a in plan.actions]},
                output_payload={
                    "actions": [{"name": a.name, "arguments": a.arguments} for a in resolved.actions],
                    "mappings": resolved.mappings,
                    "needs_follow_up": resolved.needs_follow_up,
                    "follow_up_question": resolved.follow_up_question,
                },
                status="ok",
            )
            db.commit()
            step_order += 1

            if resolved.needs_follow_up:
                follow_up = resolved.follow_up_question or "Please clarify the taxonomy mapping."
                finance.finalize_agent_run(db, run.id, assistant_output=follow_up, status="needs_follow_up")
                db.commit()
                return {"assistant_message": follow_up, "tool_outputs": [], "run_id": run.id}

            critic = self.critic.review(user_text=user_text, actions=resolved.actions, mappings=resolved.mappings)
            finance.create_agent_run_step(
                db,
                run_id=run.id,
                step_order=step_order,
                agent_name="critic_safety",
                input_payload={
                    "actions": [{"name": a.name, "arguments": a.arguments} for a in resolved.actions],
                    "mappings": resolved.mappings,
                },
                output_payload={
                    "approved": critic.approved,
                    "reason": critic.reason,
                    "needs_follow_up": critic.needs_follow_up,
                    "follow_up_question": critic.follow_up_question,
                },
                status="ok",
            )
            db.commit()
            step_order += 1

            if not critic.approved:
                follow_up = critic.follow_up_question or "I need confirmation before executing this action."
                finance.finalize_agent_run(db, run.id, assistant_output=follow_up, status="blocked")
                db.commit()
                return {"assistant_message": follow_up, "tool_outputs": [], "run_id": run.id}

            execution = self.executor.execute(
                actions=resolved.actions,
                run_tool=run_tool,
                mapping_notes=resolved.mappings,
            )

            finance.create_agent_run_step(
                db,
                run_id=run.id,
                step_order=step_order,
                agent_name="executor",
                input_payload={"actions": [{"name": a.name, "arguments": a.arguments} for a in resolved.actions]},
                output_payload={
                    "assistant_message": execution.assistant_message,
                    "tool_outputs": execution.tool_outputs,
                },
                status="ok",
            )
            db.commit()

            status = "completed"
            if execution.tool_outputs and any(not item.get("output", {}).get("ok") for item in execution.tool_outputs):
                status = "failed"

            finance.finalize_agent_run(db, run.id, assistant_output=execution.assistant_message, status=status)
            db.commit()
            return {
                "assistant_message": execution.assistant_message,
                "tool_outputs": execution.tool_outputs,
                "run_id": run.id,
            }

        except Exception as exc:  # noqa: BLE001
            db.rollback()
            try:
                finance.finalize_agent_run(db, run.id, assistant_output=f"agent error: {exc}", status="failed")
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            return {"assistant_message": f"agent error: {exc}", "tool_outputs": [], "run_id": run.id}
        finally:
            db.close()
