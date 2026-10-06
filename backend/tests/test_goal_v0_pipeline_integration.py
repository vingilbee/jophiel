from app.ai.Cognitive.system.v0.cognitive_pipeline_v0.errors import PipelineFailureStage

def test_pipeline_has_goal_failure_stages():
    assert "goal_update" in PipelineFailureStage.__args__
    assert "goal_handoff_validation" in PipelineFailureStage.__args__
